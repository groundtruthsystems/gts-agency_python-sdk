# Implementation Plan — Files Delegate Client (issue #1)

Methodology: TDD per `conductor/workflow.md` (Red → Green → Refactor), >80% coverage,
commit after every task with a Git Notes summary.

## Phase 1: Test Scaffolding & DTOs

- [ ] Task: Create test package scaffolding
    - [ ] Create `agency_sdk/test/__init__.py` (first test package in the repo; mypy overrides for `agency_sdk.test.*` already exist in pyproject)
    - [ ] Add a shared stub/monkeypatch helper for `requests` so all client tests run offline
- [ ] Task: Files DTOs
    - [ ] Write Tests: deserialisation of `FileEntry`, `FilesPagedResult`, `UploadResult`, `SignedUrlResponse` from JSON samples transcribed from the Rust DTOs (incl. `content_type: null`, root `folder_path: ""`)
    - [ ] Implement: `agency_sdk/delegates/files_dto.py` reusing `Page` from `datasets_dto`
- [ ] Task: Conductor - User Manual Verification 'Phase 1: Test Scaffolding & DTOs' (Protocol in workflow.md)

## Phase 2: Client Read & Delete Paths

- [ ] Task: `AgencyFilesClient` core with `list()`
    - [ ] Write Tests: URL `/api/files`, params `o/path/p/s` (defaults `path=""`, `size=50`), Bearer header, paged result parsing
    - [ ] Implement: client class, `_make_request` helper, `list()`
- [ ] Task: `signed_url()`
    - [ ] Write Tests: URL `/api/files/{id}/_signed-url`, `expires` omitted vs set, response parsing; HTTP error propagation (404/400 → `requests.HTTPError`)
    - [ ] Implement: `signed_url()`
- [ ] Task: `delete_file()` and `delete_folder()`
    - [ ] Write Tests: `DELETE /api/files/{id}?o` and `DELETE /api/files/_folder?o&path`, both returning `None`
    - [ ] Implement: both delete methods
- [ ] Task: Conductor - User Manual Verification 'Phase 2: Client Read & Delete Paths' (Protocol in workflow.md)

## Phase 3: Write Paths & gtsf:// Resolution

- [ ] Task: `create_folder()`
    - [ ] Write Tests: `POST /api/files/_folder` body `{"folder_path", "name"}`, `FileEntry` response
    - [ ] Implement: `create_folder()`
- [ ] Task: `upload()` (multipart)
    - [ ] Write Tests: `POST /api/files/_upload?o&path`, repeated `file` multipart fields with filename + guessed content type, no JSON content-type header, 300 s timeout, multiple files in one request
    - [ ] Implement: `upload()` with `contextlib.ExitStack`-managed handles and `mimetypes` guessing
- [ ] Task: `resolve_gtsf_uri()`
    - [ ] Write Tests: accept/reject matrix — valid `gtsf://<id>`; rejects bad scheme, empty id, embedded `/`, uppercase scheme — all `ValueError` raised before any network call
    - [ ] Implement: strict parser delegating to `signed_url()`
- [ ] Task: `download()`
    - [ ] Write Tests: signed-url fetch then streamed GET to target path, parent dirs created, bytes written match, returns `FileEntry`
    - [ ] Implement: `download()` with `stream=True` + `iter_content`
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Write Paths & gtsf:// Resolution' (Protocol in workflow.md)

## Phase 4: Facade, Cleanup & Companion Artifacts

- [ ] Task: Facade integration
    - [ ] Write Tests: `AgencyClient.files()` returns an `AgencyFilesClient` sharing `token_supplier` and `base_url`
    - [ ] Implement: compose `AgencyFilesClient` in `client.py`, add `files()` accessor
- [ ] Task: Drive-by cleanup — remove debug `print(result)` from `rules_client.py` `execute()`
- [ ] Task: Companion artifacts (Delegate Delivery Checklist)
    - [ ] Write `examples/quick_files.py` — self-verifying lifecycle script (E2E-ready for the follow-up track)
    - [ ] Update README (Delegate Clients list + files usage snippet)
    - [ ] Update CLAUDE.md (register `files_client.py` / `files_dto.py` in the architecture section)
- [ ] Task: Static gates & coverage
    - [ ] Run `mypy agency_sdk/` (strict) — exit 0
    - [ ] Run `black --check agency_sdk/ examples/` — exit 0
    - [ ] Run `bandit -r agency_sdk/` — no findings
    - [ ] Run `pytest --cov=agency_sdk --cov-report=term-missing` — new modules >80%
- [ ] Task: Conductor - User Manual Verification 'Phase 4: Facade, Cleanup & Companion Artifacts' (Protocol in workflow.md)
