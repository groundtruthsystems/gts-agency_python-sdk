# Implementation Plan — Files Delegate Client (issue #1)

Methodology: TDD per `conductor/workflow.md` (Red → Green → Refactor), >80% coverage,
commit after every task with a Git Notes summary.

## Phase 1: Test Scaffolding & DTOs [checkpoint: 9e5369e]

- [x] Task: Create test package scaffolding `9c7b772`
    - [ ] Create `agency_sdk/test/__init__.py` (first test package in the repo; mypy overrides for `agency_sdk.test.*` already exist in pyproject)
    - [ ] Add a shared stub/monkeypatch helper for `requests` so all client tests run offline
- [x] Task: Files DTOs `9a9e15e`
    - [ ] Write Tests: deserialisation of `FileEntry`, `FilesPagedResult`, `UploadResult`, `SignedUrlResponse` from JSON samples transcribed from the Rust DTOs (incl. `content_type: null`, root `folder_path: ""`)
    - [ ] Implement: `agency_sdk/delegates/files_dto.py` reusing `Page` from `datasets_dto`
- [x] Task: Conductor - User Manual Verification 'Phase 1: Test Scaffolding & DTOs' (Protocol in workflow.md) `9e5369e`

## Phase 2: Client Read & Delete Paths [checkpoint: 0d77740]

- [x] Task: `AgencyFilesClient` core with `list()` `3b60720`
    - [ ] Write Tests: URL `/api/files`, params `o/path/p/s` (defaults `path=""`, `size=50`), Bearer header, paged result parsing
    - [ ] Implement: client class, `_make_request` helper, `list()`
- [x] Task: `signed_url()` `e6681f3`
    - [ ] Write Tests: URL `/api/files/{id}/_signed-url`, `expires` omitted vs set, response parsing; HTTP error propagation (404/400 → `requests.HTTPError`)
    - [ ] Implement: `signed_url()`
- [x] Task: `delete_file()` and `delete_folder()` `fff1c56`
    - [ ] Write Tests: `DELETE /api/files/{id}?o` and `DELETE /api/files/_folder?o&path`, both returning `None`
    - [ ] Implement: both delete methods
- [x] Task: Conductor - User Manual Verification 'Phase 2: Client Read & Delete Paths' (Protocol in workflow.md) `0d77740`

## Phase 3: Write Paths & gtsf:// Resolution [checkpoint: d5ed11a]

- [x] Task: `create_folder()` `8152c6c`
    - [ ] Write Tests: `POST /api/files/_folder` body `{"folder_path", "name"}`, `FileEntry` response
    - [ ] Implement: `create_folder()`
- [x] Task: `upload()` (multipart) `7611383`
    - [ ] Write Tests: `POST /api/files/_upload?o&path`, repeated `file` multipart fields with filename + guessed content type, no JSON content-type header, 300 s timeout, multiple files in one request
    - [ ] Implement: `upload()` with `contextlib.ExitStack`-managed handles and `mimetypes` guessing
- [x] Task: `resolve_gtsf_uri()` `c0e8642`
    - [ ] Write Tests: accept/reject matrix — valid `gtsf://<id>`; rejects bad scheme, empty id, embedded `/`, uppercase scheme — all `ValueError` raised before any network call
    - [ ] Implement: strict parser delegating to `signed_url()`
- [x] Task: `download()` `181e424`
    - [ ] Write Tests: signed-url fetch then streamed GET to target path, parent dirs created, bytes written match, returns `FileEntry`
    - [ ] Implement: `download()` with `stream=True` + `iter_content`
- [x] Task: Conductor - User Manual Verification 'Phase 3: Write Paths & gtsf:// Resolution' (Protocol in workflow.md) `d5ed11a`

## Phase 4: Facade, Cleanup & Companion Artifacts [checkpoint: 68f7f27]

- [x] Task: Facade integration `f333b7b`
    - [ ] Write Tests: `AgencyClient.files()` returns an `AgencyFilesClient` sharing `token_supplier` and `base_url`
    - [ ] Implement: compose `AgencyFilesClient` in `client.py`, add `files()` accessor
- [x] Task: Drive-by cleanup — remove debug `print(result)` from `rules_client.py` `execute()` `0234d71`
- [x] Task: Companion artifacts (Delegate Delivery Checklist) `f990fb2`
    - [ ] Write `examples/quick_files.py` — self-verifying lifecycle script (E2E-ready for the follow-up track)
    - [ ] Update README (Delegate Clients list + files usage snippet)
    - [ ] Update CLAUDE.md (register `files_client.py` / `files_dto.py` in the architecture section)
- [x] Task: Static gates & coverage `47afc92`
    - [ ] Run `mypy agency_sdk/` (strict) — exit 0
    - [ ] Run `black --check agency_sdk/ examples/` — exit 0
    - [ ] Run `bandit -r agency_sdk/` — no findings
    - [ ] Run `pytest --cov=agency_sdk --cov-report=term-missing` — new modules >80%
- [x] Task: Conductor - User Manual Verification 'Phase 4: Facade, Cleanup & Companion Artifacts' (Protocol in workflow.md) `68f7f27`
