# File Storage Flows: Upload / Download Architecture

Covers the `AgencyFilesClient` implementation
(`agency_sdk/delegates/files_client.py`) for
[issue #1](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/1).
Server-side counterpart: `gts-agency-control/src/handler/files.rs` and
`service/tenant_files/`.

Core design (asymmetric):

- **Upload**: bytes pass **through** the control plane (relay style) — validation,
  overwrite semantics, and metadata persistence happen on the byte path.
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

    FC == "1 upload: multipart byte stream<br/>POST /_upload (300s)" ==> MW
    FC -- "2 metadata/signing calls<br/>GET /_signed-url etc. (30s)" --> MW
    SVC -- "insert / soft-delete metadata rows" --> DB
    SVC == "upload: write bytes<br/>(storage credentials live here)" ==> OS
    SVC -. "download: generate V4 presigned GET URL" .-> OS
    FC == "3 download: GET signed URL directly<br/>stream=True, 1 MiB chunks (300s)" ==> OS
```

> Thick edges are **byte paths**; thin edges are **metadata/control paths**.
> Storage credentials exist only on the control plane; the SDK only ever holds a
> time-limited signed URL for downloads (default 15 minutes).

## Upload Flow (relay style)

`client.upload(organisation_id, file_paths, path)` — byte path:
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
    Note over SDK: empty file_paths → ValueError immediately<br/>(no network call)
    SDK->>IDP: only when cached JWT expired:<br/>client-credentials exchange
    IDP-->>SDK: access_token
    Note over SDK: ExitStack opens handles<br/>mimetypes guesses content type by extension<br/>builds multipart: repeated "file" fields
    SDK->>CP: POST /_upload?o=2&path=guidelines<br/>Authorization: Bearer …<br/>Content-Type: multipart/form-data; boundary=…<br/>(timeout 300s)
    Note over CP: axum DefaultBodyLimit: body ≤ 100 MiB (else 413)<br/>JWKS signature + audience + require_user
    loop each multipart field
        Note over CP: field name ≠ "file" → silently ignored<br/>missing filename or contains / \ .. → 400<br/>chunked read, per-file total ≤ 100 MiB → else 400
        CP->>DB: active row with same name in folder?<br/>→ soft-delete old row (overwrite semantics)
        CP->>OS: save_object_from_bytes<br/>key: {prefix}/{org}/{folder}/{filename}
        CP->>DB: insert new metadata row<br/>(id, size_bytes, content_type, uploaded_by…)
    end
    CP-->>SDK: 200 {"uploaded": [FileEntry…]}
    SDK-->>APP: UploadResult (Pydantic)
```

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
| Per-file upload cap | 100 MiB | server service, cumulative count (400) |
| Whole request body cap | 100 MiB (shared by multiple files) | server axum route layer (413) |
| Multipart field name | must be `file`; other names **silently ignored** | server |
| File/folder names | non-empty; no `/`, `\`, `..` | server (400) |
| Same-name upload | overwrite: old row soft-deleted, object key reused | server |
| Signed URL lifetime | default 900s, clamped [1, 604800] | server |
| Upload/download timeout | 300s (metadata/signing calls 30s) | SDK |
| Download memory peak | ≈1 MiB (streamed chunks) | SDK |
| Upload memory peak | ≈ total file size (requests builds multipart body) | SDK, bounded by the 100 MiB cap |
| `gtsf://` parsing | SDK-side convention only; malformed → `ValueError` | SDK (before any request) |

## Why the Asymmetry

Download is read-only: metadata lookup and byte retrieval separate safely, so a
presigned URL offloads bandwidth to object storage and expires on its own. Upload
is a transaction — validate + soft-delete the overwritten row + write the object +
persist metadata — and passing bytes through the control plane is the simplest way
to keep that consistent; the 100 MiB cap keeps the relay cost acceptable. If larger
files are ever needed, the evolution path is a "presigned upload session + completion
confirmation" endpoint pair (cf. Slack `completeUploadExternal` / Git LFS `verify`),
leaving the current API untouched.
