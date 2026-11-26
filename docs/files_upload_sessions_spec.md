# Spec: Files uploads route by size (proxied ≤ threshold, upload session above)

Status: draft, 2026-11-25. Counterpart server change: gts-agency branch `comand_files_upload`
(default `files.proxied_upload_max_bytes` lowered from 30 MiB to 1 MiB).

## 1. Why

`AgencyFilesClient.upload` (`agency_sdk/delegates/files_client.py`) sends every file through
`POST /api/files/_upload`, the proxied multipart route where the bytes pass through comand.

comand no longer accepts large files on that route:

- Since the large-file-upload track (gts-agency `CHANGE_LOG.md`, 2026-11-24), `_upload` only
  takes files at or under `proxied_upload_max_bytes`. Larger files must use an **upload
  session**, where the client sends the bytes straight to the bucket (a GCS resumable session,
  or S3/RustFS presigned multipart parts).
- The default threshold is now **1 MiB** (configurable up to 30 MiB). The request body limit is
  the threshold plus 1 MiB of multipart framing.

**Today's SDK fails with an HTTP 413** (`details.code = use_signed_upload`) when:

- any single file is over 1 MiB, or
- one `upload()` call batches files that total more than about 2 MiB.

The web Files page and `gts-cli files upload` already route by size. The SDK is the only
client left on the old contract.

The SDK documentation is also stale. It still describes the original 100 MiB per-file and
per-request cap in:

- the `upload()` docstring;
- the `download()` docstring;
- `README.md` §Files;
- `docs/files_storage_flows.md`.

## 2. Scope

**In scope**

- Size-based routing inside `AgencyFilesClient.upload`. The public signature and the
  `UploadResult` return type stay the same.
- New DTOs for limits and sessions.
- Doc fixes, an example update and a CHANGE_LOG entry.

**Out of scope**

- Dataset filesystem uploads (`datasets_client._upload_file`, `PUT …/filesystem/_upload`). That
  route and its limits are unchanged.
- Parallel S3 part uploads. Parts go one at a time in v1; see §8.
- Progress callbacks.
- Download changes, apart from the docstring.

## 3. Server contract (as implemented in comand)

All routes take the organisation as the `o` query parameter and require Files write permission.
Errors use the standard envelope `{error: {type, message, details: {code, ...}}}`.

| Route | Purpose | Notable responses |
| --- | --- | --- |
| `GET /api/files/_limits?o=` | Limits to route by | `200 {proxied_upload_max_bytes, max_upload_bytes, part_size}` |
| `POST /api/files/_upload?o=&path=` | Proxied multipart, field name `file`, one or more files | `200 UploadResult`; `413 use_signed_upload` (body carries `details.limits`) |
| `POST /api/files/_upload-session?o=` | Open a session for **one** file with `size > proxied_upload_max_bytes`. JSON body `{folder_path, filename, size, content_type}` | `201 UploadSessionResponse`; `400 use_proxied_upload` (size at or under the threshold); `413 file_too_large`; `429` (per-organisation caps on pending sessions and pending bytes) |
| `POST /api/files/_upload-session/{file_id}/_parts?o=` | Re-sign S3 part URLs. Body `{part_numbers: [n, …]}` | `200 {parts: [{part_number, url}], expires_at}`; `409 not_multipart` (a GCS session) |
| `POST /api/files/_upload-session/{file_id}/_complete?o=` | Server checks the object and makes the file active. Empty body | `200 FileEntry` (also when already complete); `409 upload_incomplete` (retry; on GCS, resume the transfer first); `409` with `type CONFLICT_RETRY` (retry); `422 upload_verification_failed` (server already removed the upload) |
| `DELETE /api/files/_upload-session/{file_id}?o=` | Cancel the session | `200`; `409 upload_already_complete` |

`UploadSessionResponse` fields:

- `file_id`, `part_size`, `expires_at`, `limits`.
- `strategy`: `"s3_multipart"` or `"gcs_resumable"`.
- `session_uri`: GCS only.
- `parts: [{part_number, url}]` and `parts_expire_at`: S3/RustFS only.

**Direct transfers to the bucket carry no `Authorization` header.** The presigned URL or session
URI is the credential, so it must never appear in logs, exception messages or `repr`.

**S3 multipart**

- Part `n` (numbered from 1) is the byte range `[(n-1)*part_size, min(n*part_size, size))`.
- Upload it with `PUT url`, the raw bytes as the body.
- A `403` means the signature expired or was rejected. Re-sign that part through `_parts` and
  retry.
- The client does not collect ETags. `_complete` lists the parts on the server side.

**GCS resumable**

- Send chunks of `part_size` bytes in order: `PUT session_uri` with header
  `Content-Range: bytes {start}-{end}/{size}`. Every chunk except the last is a multiple of
  256 KiB, and the server picks `part_size` to satisfy that.
- `308` means the chunk was accepted. Its `Range: bytes=0-N` header gives the next offset,
  `N+1`; with no `Range` header the next offset is 0.
- `200` or `201` means the upload is complete.
- To recover from an error, query the status with `PUT session_uri`, headers
  `Content-Range: bytes */{size}` and `Content-Length: 0`. Resume from the offset it reports.
- comand binds a GCS session to the browser `Origin` when one is present. `requests` sends no
  `Origin` header, so SDK sessions are unaffected, just like the CLI.

## 4. Required behaviour

Reference implementation: gts-agency `crates/cli/src/handler/files_upload.rs`
(`FileUploader::upload_file`). Mirror its decisions so the two clients behave the same.

### 4.1 Routing — `upload(organisation_id, file_paths, path="")`

1. **Check locally first.** Raise `ValueError` for an empty `file_paths`, and for any path that
   is missing or is not a regular file. All of this happens before any network call.
2. **Fetch the limits** with `GET /_limits` once per `upload()` call.
3. **Refuse oversized files locally.** A file larger than `max_upload_bytes` raises
   `ValueError` naming the file, before any write.
4. **Proxied path** for files at or under `proxied_upload_max_bytes`:
   - Group them into `_upload` batches whose total stays at or under `proxied_upload_max_bytes`.
     That keeps every request within the server body limit (threshold + 1 MiB of framing).
   - A single file at the threshold goes alone.
   - Batch order follows `file_paths`.
5. **Session path** for files over `proxied_upload_max_bytes`: one session per file, in
   `file_paths` order (§4.2).
6. **Fallbacks**, matching the CLI's handling of a stale threshold:
   - If `_limits` fails, try `_upload` file by file. A `413 use_signed_upload` sends that file
     through a session.
   - If initiate answers `400 use_proxied_upload`, the file goes through `_upload` instead.
7. **Return value.** Return one `UploadResult` whose `uploaded` list holds every `FileEntry`, in
   `file_paths` order:
   - proxied batches contribute their `uploaded` entries;
   - sessions contribute the `FileEntry` that `_complete` returns.
8. **Partial failure.**
   - Files run in order, and the first unrecoverable failure stops the call.
   - Raise a new `FilesUploadError(requests.HTTPError)` subclass. Its `uploaded` attribute
     holds the entries that already succeeded, and its `__cause__` is the original error.
   - Subclassing `HTTPError` keeps existing `except requests.HTTPError` callers working.
   - Files already uploaded stay uploaded. Uploads overwrite by name, so retrying the call is
     safe.

### 4.2 Upload session (one file)

1. **Initiate** with `POST /_upload-session` and body
   `{folder_path: path, filename: <basename>, size, content_type: mimetypes guess or null}`.
2. **Transfer**, by `strategy`:
   - `s3_multipart`: read each part from disk with seek and read, one at a time, and `PUT` it.
     Retry up to `max_retries` (default 3) with exponential backoff (base 0.5 s, doubling).
     On a `403`, drop that part's cached URL, re-sign through `_parts` and retry at once.
   - `gcs_resumable`: send chunks in order. A `308` advances the offset. On a connection error
     or a 5xx, back off, query the status and resume from the reported offset. Fail after
     `max_retries` consecutive attempts that make no progress.
   - Any other strategy: fail with `unsupported upload strategy`.
3. **Complete** with `POST …/_complete`.
   - Retry `409 CONFLICT_RETRY` and `409 upload_incomplete` up to `max_retries` with backoff.
     On `upload_incomplete` for GCS, resume the transfer from the status query before retrying.
   - `422` is final: raise, and do not cancel, because the server has already removed the
     upload.
4. **Cancel on failure.** After a successful initiate, any unrecoverable failure (including
   `KeyboardInterrupt`, via `try/finally`) sends a best-effort `DELETE /_upload-session/{id}`.
   Ignore errors from that cancel, including `409 upload_already_complete`. Then raise the
   original error.
5. **No credentials in errors.** Exceptions and log lines for direct PUTs name the part number
   or byte offset and the status code, never the URL. Build messages by hand; do not rely on
   `requests`' default `HTTPError` text, which includes the URL.
6. **Timeouts.**
   - Calls to comand: the existing delegate timeout.
   - Direct PUTs: `(connect=10, read=300)` per request.

### 4.3 Memory

- **Proxied batches** are built in memory by `requests`, so the peak is about twice the batch
  size: at most 2 MiB at the default threshold and 60 MiB at the 30 MiB maximum. That is
  acceptable, and **no new dependency** (such as `requests-toolbelt`) is needed.
- **Session transfers** hold one part in memory at a time, about `part_size` (64 MiB by
  default). Never read the whole file. Document this peak in the docstring.

### 4.4 Configuration

Expose `max_retries` and `retry_base_delay` as constructor keyword arguments on
`AgencyFilesClient`, with defaults of 3 and 0.5 s. Tests set the delay to 0. There are no new
arguments on `upload()`.

## 5. Code changes

| File | Change |
| --- | --- |
| `agency_sdk/delegates/files_dto.py` | Add `UploadLimits`, `UploadPartUrl` (custom `__repr__` that redacts `url`), `UploadSessionResponse` (redact `session_uri`/`parts` in `repr`; `session_uri`, `parts` and `parts_expire_at` are optional) and `UploadPartsResponse`. Use pydantic `Field(repr=False)` for the credential fields. |
| `agency_sdk/delegates/files_client.py` | Add `limits(organisation_id) -> UploadLimits` as a public method, useful to callers. Rewrite `upload()` per §4.1. Add private helpers `_proxied_upload`, `_session_upload`, `_s3_transfer`, `_gcs_transfer`, `_gcs_status`, `_complete` and `_abort`. Add `FilesUploadError`. Fix the `upload`/`download` docstrings: 5 GiB maximum, routing by `_limits`. |
| `agency_sdk/delegates/__init__.py` / package exports | Export `FilesUploadError` and `UploadLimits` wherever the other files DTOs are exported. |
| `agency_sdk/test/conftest.py` | `stub_requests` patches only `request`, `get` and `post`. Add `put` and `delete`, and let `StubResponse` carry headers (`Range`) and a status of 308. |
| `agency_sdk/test/test_files_client.py`, `test_files_dto.py` | Tests in §6. |
| `README.md` §Files | Replace "up to 100 MiB per file and per request" with the routing description and the 5 GiB maximum. |
| `docs/files_storage_flows.md` | Upload is no longer "always relayed". Add the session flow as a sequence diagram covering S3 and GCS, update the limits table (threshold from `_limits`, 1 MiB default, 5 GiB maximum), and update the memory row. |
| `examples/quick_files.py` | Also upload a file of about 3 MiB so the session path gets exercised against a live stack. |
| `CHANGE_LOG.md` | New entry. The behaviour change is not breaking: the same signature, larger files now work, and errors are `HTTPError` subclasses. |
| `pyproject.toml` | Bump the pre-release version according to the repo's release practice. |

## 6. Tests (offline, through `stub_requests`)

1. **Routing at the boundary.** With `_limits` reporting a 1 MiB threshold:
   - a file of exactly 1 MiB goes to `_upload`;
   - a file of 1 MiB + 1 byte goes to `_upload-session`.
2. **Batching.** Five 300 KiB files with a 1 MiB threshold give two `_upload` calls (3 files,
   then 2), and the result order matches `file_paths`.
3. **Too large.** A file over `max_upload_bytes` raises `ValueError` with no write calls made.
4. **S3 happy path.** A file of 2.5 parts gives three PUTs with the correct byte ranges, then
   `_complete`. The PUTs carry no `Authorization` header.
5. **S3 403 re-sign.** A 403 on part 2 leads to `_parts` with `{part_numbers:[2]}`, a retry with
   the new URL, and success.
6. **S3 exhausted retries.** Repeated 5xx responses on one part raise `FilesUploadError`, the
   error does not contain the part URL, and `DELETE /_upload-session/{id}` was called.
7. **GCS happy path.**
   - The chunks carry the correct `Content-Range` headers.
   - A `308` with `Range: bytes=0-N` advances the offset to N+1.
   - The final `200` completes the transfer, then `_complete` is called.
8. **GCS resume.** A connection error on chunk 2 leads to a status query (`bytes */size`), the
   `308` reply gives the offset, and the upload resumes from it.
9. **Completing.**
   - `409 upload_incomplete` is retried and then succeeds.
   - `409 CONFLICT_RETRY` is retried.
   - `422` raises and does **not** call `DELETE`.
10. **Fallbacks.**
    - If `_limits` fails, the file goes to `_upload`, a `413 use_signed_upload` follows, and the
      file goes through a session.
    - If initiate answers `400 use_proxied_upload`, the file goes to `_upload`.
11. **Partial failure.** The first file succeeds and the second fails. `FilesUploadError.uploaded`
    holds the first entry, and the error is an instance of `requests.HTTPError`.
12. **Redaction.** `repr()` of `UploadSessionResponse` and `UploadPartUrl` shows no URL.
13. **Memory.** A three-part file is read part by part. Assert with a spy on file reads that no
    read is larger than `part_size`.

**Live check** (manual, gts-agency local stack with RustFS and comand on :13001):

- `examples/quick_files.py` uploads a 500 KiB file and a 3 MiB file.
- `list` shows both with the right `size_bytes`.
- Downloading the 3 MiB file returns identical bytes (compare SHA-256).

## 7. Gates

`pytest`, `mypy agency_sdk/` (strict), `black --check agency_sdk/`, and
`bandit -r agency_sdk/ -x agency_sdk/test` (the release gate fails on any finding; direct PUTs
need explicit timeouts to stay clean of B113).

## 8. Open questions and deferred work

- **Parallel S3 parts.** The CLI sends 2 parts at a time. v1 sends them one at a time to keep the
  memory peak at a single part. Add a `parallelism` option later if throughput matters.
- **Progress callback.** The CLI has one; the SDK has none today. Defer.
- **Async client.** None exists; nothing to do.
