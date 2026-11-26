# File Storage Flows: Upload / Download Architecture

Covers the `AgencyFilesClient` implementation
(`agency_sdk/delegates/files_client.py`) for
[issue #1](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/1).
Server-side counterpart: `gts-agency-control/src/handler/files.rs` and
`service/tenant_files/`.

Core design:

- **Upload routes by size.** `upload()` reads the limits from `GET /api/files/_limits`
  once per call:
  - Files at or under `proxied_upload_max_bytes` (1 MiB by default, configurable up to
    30 MiB) pass **through** the control plane (relay style, `POST /_upload`), batched so
    each request stays within that threshold.
  - Larger files, up to `max_upload_bytes` (5 GiB by default), use an **upload session**:
    the control plane hands out presigned S3 part URLs or a GCS resumable session URI, the
    SDK sends the bytes **straight to the bucket**, and `_complete` verifies the object
    and makes the file active.
- **Download**: bytes **bypass** the control plane (presigned direct-to-storage) —
  the control plane only issues a short-lived signed URL; object storage carries
  the bandwidth.

## Component Architecture

```mermaid
flowchart LR
    subgraph client["SDK process (agency_sdk)"]
        APP["caller code"]
        FC["AgencyFilesClient"]
        CS["CredentialsSupplier<br/>(JWT cache/refresh)"]
        APP --> FC
        FC --> CS
    end

    IDP["Auth service<br/>(OAuth2 client-credentials)"]
    CS -. "refresh on expiry" .-> IDP

    subgraph cp["gts-agency-control (control plane)"]
        MW["auth middleware<br/>JWKS verify + require_user"]
        H["files handler<br/>/api/files/*"]
        SVC["TenantFileStorageService<br/>validation / overwrite / signing"]
        MW --> H --> SVC
    end

    DB[("MySQL<br/>tenant_files metadata<br/>(source of truth)")]
    OS[("Object storage<br/>GCS / S3 / MinIO<br/>{prefix}/{org}/{folder}/{file}")]

    FC == "1a small upload: multipart byte stream<br/>POST /_upload (300s)" ==> MW
    FC == "1b large upload: PUT parts / chunks<br/>to presigned URL (10s connect, 300s read)" ==> OS
    FC -- "2 metadata/signing calls<br/>GET /_signed-url etc. (30s)" --> MW
    SVC -- "insert / soft-delete metadata rows" --> DB
    SVC == "upload: write bytes<br/>(storage credentials live here)" ==> OS
    SVC -. "download: generate V4 presigned GET URL" .-> OS
    FC == "3 download: GET signed URL directly<br/>stream=True, 1 MiB chunks (300s)" ==> OS
```

> Thick edges are **byte paths**; thin edges are **metadata/control paths**.
> Storage credentials exist only on the control plane; the SDK only ever holds
> time-limited signed URLs (downloads, default 15 minutes) and upload-session URLs.
> Those URLs are credentials: the SDK never puts them in exception messages, logs or
> `repr`.

## Upload Flow: proxied (files at or under the threshold)

`client.upload(organisation_id, file_paths, path)` for small files — byte path:
local disk → SDK process memory → HTTP request body → control plane memory →
object storage.

```mermaid
sequenceDiagram
    autonumber
    participant APP as Caller
    participant SDK as AgencyFilesClient.upload()
    participant IDP as Auth service
    participant CP as Control plane /api/files/_upload
    participant DB as MySQL (metadata)
    participant OS as Object storage

    APP->>SDK: upload(o=2, [a.pdf, b.txt], path="guidelines")
    Note over SDK: empty list, missing file or not a regular file → ValueError<br/>(no network call)
    SDK->>CP: GET /_limits?o=2
    CP-->>SDK: {proxied_upload_max_bytes, max_upload_bytes, part_size}
    Note over SDK: file > max_upload_bytes → ValueError (no write)<br/>group files ≤ threshold into batches, total ≤ threshold
    SDK->>IDP: only when cached JWT expired:<br/>client-credentials exchange
    IDP-->>SDK: access_token
    Note over SDK: ExitStack opens handles<br/>mimetypes guesses content type by extension<br/>builds multipart: repeated "file" fields
    SDK->>CP: POST /_upload?o=2&path=guidelines<br/>Authorization: Bearer …<br/>Content-Type: multipart/form-data; boundary=…<br/>(timeout 300s)
    Note over CP: body ≤ threshold + 1 MiB framing<br/>(else 413 use_signed_upload)<br/>JWKS signature + audience + require_user
    loop each multipart field
        Note over CP: field name ≠ "file" → silently ignored<br/>missing filename or contains / \ .. → 400<br/>file over the threshold → 413 use_signed_upload
        CP->>DB: active row with same name in folder?<br/>→ soft-delete old row (overwrite semantics)
        CP->>OS: save_object_from_bytes<br/>key: {prefix}/{org}/{folder}/{filename}
        CP->>DB: insert new metadata row<br/>(id, size_bytes, content_type, uploaded_by…)
    end
    CP-->>SDK: 200 {"uploaded": [FileEntry…]}
    SDK-->>APP: UploadResult (Pydantic)
```

If `_limits` cannot be fetched, the SDK sends each file to `_upload` on its own and
moves it to an upload session when comand answers `413 use_signed_upload`.

## Upload Flow: upload session (files over the threshold)

One session per file, in `file_paths` order. Byte path: local disk → one part in SDK
memory → object storage, **never through the control plane**.

```mermaid
sequenceDiagram
    autonumber
    participant SDK as AgencyFilesClient.upload()
    participant CP as Control plane /api/files
    participant OS as Object storage

    SDK->>CP: POST /_upload-session?o=2<br/>{folder_path, filename, size, content_type}
    Note over CP: size ≤ threshold → 400 use_proxied_upload<br/>(SDK sends the file to _upload instead)<br/>size > max → 413 file_too_large · pending caps → 429
    CP-->>SDK: 201 {file_id, strategy, part_size, parts | session_uri, …}
    alt strategy = s3_multipart (S3 / RustFS)
        loop part n = 1..ceil(size / part_size)
            SDK->>SDK: seek + read bytes [(n-1)·part_size, n·part_size)
            SDK->>OS: PUT parts[n].url (raw bytes, no Authorization)
            Note over SDK,OS: 403 → POST /_upload-session/{id}/_parts {part_numbers:[n]}, retry now<br/>5xx / connection error → backoff 0.5 s × 2^attempt, max_retries
        end
    else strategy = gcs_resumable
        loop chunk from offset
            SDK->>OS: PUT session_uri<br/>Content-Range: bytes start-end/size
            OS-->>SDK: 308 Range: bytes=0-N → next offset N+1 · 200/201 → done
            Note over SDK,OS: error → backoff, then PUT Content-Range: bytes */size<br/>(Content-Length: 0) and resume from the reported offset
        end
    end
    SDK->>CP: POST /_upload-session/{id}/_complete
    Note over CP: checks the object, makes the file active<br/>409 upload_incomplete / CONFLICT_RETRY → SDK retries<br/>(GCS: resumes the transfer first)<br/>422 upload_verification_failed → upload already removed
    CP-->>SDK: 200 FileEntry
    Note over SDK,CP: any other failure after initiate (incl. Ctrl-C):<br/>best-effort DELETE /_upload-session/{id}, then raise
```

The first unrecoverable failure stops `upload()` with `FilesUploadError` (a
`requests.HTTPError`). Its `uploaded` lists the files that already succeeded; uploads
overwrite by name, so retrying the call is safe.

## Download Flow (presigned direct, two steps)

`client.download(file_id, organisation_id, target_path)` — byte path:
object storage → SDK process (1 MiB chunks) → local disk; **never through the
control plane**.

```mermaid
sequenceDiagram
    autonumber
    participant APP as Caller
    participant SDK as AgencyFilesClient
    participant CP as Control plane /api/files
    participant DB as MySQL (metadata)
    participant OS as Object storage

    APP->>SDK: download(file_id, o=2, target_path)<br/>or resolve_gtsf_uri("gtsf://<id>", o=2)
    Note over SDK: gtsf:// entry point: strict parsing<br/>lowercase scheme / non-empty id without "/"<br/>malformed → ValueError (zero network calls)
    SDK->>CP: GET /{id}/_signed-url?o=2 [&expires=N]<br/>Authorization: Bearer … (timeout 30s)
    CP->>DB: look up metadata by id + org
    Note over CP: missing → 404<br/>folder → 400 "Cannot sign a folder"<br/>expires defaults 900s, clamped [1, 604800]
    CP->>OS: generate V4 presigned GET URL (no bytes moved)
    CP-->>SDK: 200 {signed_url, expires_at, file: FileEntry}
    Note over SDK: resolve_gtsf_uri() returns here<br/>download() continues below
    SDK->>SDK: target.parent.mkdir(parents=True)
    SDK->>OS: GET signed_url (no Bearer header,<br/>the signature is the credential)<br/>stream=True, timeout 300s
    loop iter_content(1 MiB)
        OS-->>SDK: byte chunk
        SDK->>SDK: sequential write to target file
    end
    SDK-->>APP: FileEntry (metadata from step one)
```

## Constraint Quick Reference

| Constraint | Value | Enforced by |
|---|---|---|
| Proxied threshold (`proxied_upload_max_bytes`) | 1 MiB default, up to 30 MiB; read from `GET /_limits` | server (413 `use_signed_upload`) |
| Proxied request body cap | threshold + 1 MiB of multipart framing; the SDK batches within the threshold | server (413) |
| Per-file maximum (`max_upload_bytes`) | 5 GiB default | SDK (`ValueError`, before any write) and server (413 `file_too_large`) |
| Session part size | 64 MiB default; GCS chunks are multiples of 256 KiB | server |
| Multipart field name | must be `file`; other names **silently ignored** | server |
| File/folder names | non-empty; no `/`, `\`, `..` | server (400) |
| Same-name upload | overwrite: old row soft-deleted, object key reused | server |
| Signed URL lifetime | default 900s, clamped [1, 604800] | server |
| Upload/download timeout | 300s (metadata/signing/session calls 30s; direct PUTs 10s connect, 300s read) | SDK |
| Upload retries | `max_retries=3`, backoff `retry_base_delay=0.5` s doubling (constructor kwargs) | SDK |
| Download memory peak | ≈1 MiB (streamed chunks) | SDK |
| Upload memory peak | proxied: ≈ 2 × batch size (2 MiB at the default threshold); session: ≈ one `part_size` | SDK |
| `gtsf://` parsing | SDK-side convention only; malformed → `ValueError` | SDK (before any request) |

## Why the Asymmetry

Download is read-only: metadata lookup and byte retrieval separate safely, so a
presigned URL offloads bandwidth to object storage and expires on its own. Upload
is a transaction — validate + soft-delete the overwritten row + write the object +
persist metadata. For small files, passing bytes through the control plane is the
simplest way to keep that consistent. Large files would make the relay expensive, so
they use an upload session instead: the bytes go straight to the bucket, and the
`_complete` call does the validation and metadata work once the object is there.
