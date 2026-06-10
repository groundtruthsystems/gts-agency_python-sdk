# Specification — Files Delegate Client (issue #1)

## Overview

Add a sixth delegate to the SDK covering the GTS control plane's tenant file storage
API (`/api/files`), per
[groundtruthsystems/gts-agency_python-sdk#1](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/1).
The server implementation is `gts-agency-control/src/handler/files.rs` +
`service/tenant_files/` (merged 2026-06-08); all wire-format facts below were
verified against that source.

The client provides file/folder management, signed-URL retrieval, and resolution of
`gtsf://<file_id>` URIs — the convention used in GTS configurations and rule
annotations to reference stored files. The `gtsf://` scheme has no server-side
implementation; parsing is entirely the SDK's responsibility.

## Verified Wire-Format Facts (server source of truth)

- All endpoints under `/api/files`, Bearer JWT auth, org scope via `o` query param.
- Pagination response: `{"page": {"page", "size", "total"}, "items": [...]}` —
  identical to the existing `Page` type in `datasets_dto.py`. Server default page
  size: 50.
- All DTO fields are snake_case (serde defaults, no camelCase aliases).
- Timestamps are strings formatted `%Y-%m-%dT%H:%M:%SZ`.
- Upload: multipart field name must be `file` (repeatable); other field names are
  silently ignored; missing filename → 400. Per-file cap 100 MiB AND whole request
  body capped at 100 MiB (axum `DefaultBodyLimit`). Same-name upload soft-deletes
  the existing row (overwrite semantics). Zero `file` fields → 400.
- Signed URL: default `expires` 900 s, server-clamped to [1, 604800]; folder → 400
  "Cannot sign a folder"; missing file → 404.
- Names must be non-empty and must not contain `/`, `\`, `..` → 400. Duplicate
  folder name → 409.
- `folder_path` is normalised server-side (slashes trimmed); root is `""`.
- `DELETE /api/files/{id}` on a folder → 400 (use folder delete);
  `DELETE /api/files/_folder` requires `path` and recursively soft-deletes.
- Both deletes return `{"status": "deleted"}`.

## Functional Requirements

### FR1 — DTO module `agency_sdk/delegates/files_dto.py`

snake_case Pydantic v2 models (dataset style), reusing `Page` from `datasets_dto`:

- `FileEntry`: `id: str`, `name: str`, `folder_path: str`, `path: str`,
  `is_folder: bool`, `content_type: str | None = None`, `size_bytes: int`,
  `uploaded_by: int`, `created_on: str`
- `FilesPagedResult`: `page: Page`, `items: list[FileEntry]`
- `UploadResult`: `uploaded: list[FileEntry]`
- `SignedUrlResponse`: `signed_url: str`, `expires_at: str`, `file: FileEntry`
  (module-local name; intentionally distinct from `datasets_dto.SignedUrlResponse`
  which carries `file_info`)

### FR2 — Client `agency_sdk/delegates/files_client.py` (`AgencyFilesClient`)

Follows the delegate pattern (`_make_request` helper, `raise_for_status()`, 30 s
default timeout, query param abbreviations):

- `list(organisation_id: int, path: str = "", page: int = 0, size: int = 50) -> FilesPagedResult`
  — `GET /api/files?o&path&p&s`
- `upload(organisation_id: int, file_paths: list[str | Path], path: str = "") -> UploadResult`
  — `POST /api/files/_upload` multipart; dedicated request path (no JSON
  content-type header); `contextlib.ExitStack`-managed file handles streamed by
  requests; content type guessed via `mimetypes`; 300 s timeout; size caps
  documented in the docstring, no client-side pre-check
- `create_folder(organisation_id: int, name: str, folder_path: str = "") -> FileEntry`
  — `POST /api/files/_folder` body `{"folder_path", "name"}`
- `delete_folder(organisation_id: int, path: str) -> None`
  — `DELETE /api/files/_folder?o&path`
- `signed_url(file_id: str, organisation_id: int, expires: int | None = None) -> SignedUrlResponse`
  — `GET /api/files/{id}/_signed-url?o[&expires]`
- `delete_file(file_id: str, organisation_id: int) -> None`
  — `DELETE /api/files/{id}?o`
- `resolve_gtsf_uri(uri: str, organisation_id: int, expires: int | None = None) -> SignedUrlResponse`
  — strict parse: lowercase `gtsf://` prefix, non-empty remainder without `/`,
  else `ValueError` before any network call; delegates to `signed_url`
- `download(file_id: str, organisation_id: int, target_path: str | Path) -> FileEntry`
  — `signed_url()` then streamed GET (`stream=True`, `iter_content`, 300 s
  timeout), creates parent dirs, returns the file metadata

### FR3 — Facade integration

`AgencyClient` composes `AgencyFilesClient` and exposes `files() -> AgencyFilesClient`.

### FR4 — Companion artifacts (Delegate Delivery Checklist)

- `examples/quick_files.py` — runnable lifecycle example (list → create folder →
  upload → list → signed URL → `gtsf://` resolve → download → delete file →
  delete folder), self-verifying with assertions and unconditional cleanup
- README: Delegate Clients list + files usage snippet
- CLAUDE.md: register the new delegate pair in the architecture section

### FR5 — Drive-by cleanup

Remove the stray debug `print(result)` in `rules_client.py` `execute()`.

## Non-Functional Requirements

- mypy strict passes for all production code; `agency_sdk/test/` is relaxed per
  existing pyproject overrides
- black, line length 120
- bandit clean (CI security gate parity)
- No new runtime dependencies
- Errors propagate untouched (`raise_for_status()`); client-side validation raises
  `ValueError`

## Acceptance Criteria

1. `mypy agency_sdk/` (strict) exits 0
2. `black --check agency_sdk/ examples/` exits 0
3. `bandit -r agency_sdk/` reports no issues
4. Offline test suite in `agency_sdk/test/` passes with no network access:
   - per-method protocol tests (URL, query params, headers, multipart field name
     `file`, body shape) via monkeypatched `requests`
   - DTO deserialisation tests against JSON samples transcribed from the Rust DTOs
   - `resolve_gtsf_uri` accept/reject matrix (valid id; bad scheme, empty id,
     embedded slash, uppercase scheme)
   - error-path tests (HTTP error propagation, `ValueError` before network on bad
     URI)
5. >80% coverage for the new modules
6. `AgencyClient.files()` returns a working `AgencyFilesClient`

## Out of Scope

- **Real E2E verification against a live control plane** (dev environment or local
  full stack) — deferred to a follow-up track; the example script is written E2E-ready
- Async API, retry/backoff, custom exception hierarchy (per product guidelines)
- In-memory/bytes upload variant (only local file paths in this track)
- Handling of the committed dev credentials in `quick_execute_rule.py` (accepted by
  product guidelines decision)
