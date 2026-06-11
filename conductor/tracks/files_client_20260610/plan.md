# Implementation Plan — Files Delegate Client (issue #1)

Methodology: TDD per `conductor/workflow.md` (Red → Green → Refactor), >80% coverage,
commit after every task with a Git Notes summary.

## Phase 1: Test Scaffolding & DTOs [checkpoint: ade8546]

- [x] Task: Create test package scaffolding `a1d9e00`
    - [ ] Create `agency_sdk/test/__init__.py` (first test package in the repo; mypy overrides for `agency_sdk.test.*` already exist in pyproject)
    - [ ] Add a shared stub/monkeypatch helper for `requests` so all client tests run offline
- [x] Task: Files DTOs `881e898`
    - [ ] Write Tests: deserialisation of `FileEntry`, `FilesPagedResult`, `UploadResult`, `SignedUrlResponse` from JSON samples transcribed from the Rust DTOs (incl. `content_type: null`, root `folder_path: ""`)
    - [ ] Implement: `agency_sdk/delegates/files_dto.py` reusing `Page` from `datasets_dto`
- [x] Task: Conductor - User Manual Verification 'Phase 1: Test Scaffolding & DTOs' (Protocol in workflow.md) `ade8546`

## Phase 2: Client Read & Delete Paths [checkpoint: d470057]

- [x] Task: `AgencyFilesClient` core with `list()` `6dbadc3`
    - [ ] Write Tests: URL `/api/files`, params `o/path/p/s` (defaults `path=""`, `size=50`), Bearer header, paged result parsing
    - [ ] Implement: client class, `_make_request` helper, `list()`
- [x] Task: `signed_url()` `4fed5dd`
    - [ ] Write Tests: URL `/api/files/{id}/_signed-url`, `expires` omitted vs set, response parsing; HTTP error propagation (404/400 → `requests.HTTPError`)
    - [ ] Implement: `signed_url()`
- [x] Task: `delete_file()` and `delete_folder()` `4b375c6`
    - [ ] Write Tests: `DELETE /api/files/{id}?o` and `DELETE /api/files/_folder?o&path`, both returning `None`
    - [ ] Implement: both delete methods
- [x] Task: Conductor - User Manual Verification 'Phase 2: Client Read & Delete Paths' (Protocol in workflow.md) `d470057`

## Phase 3: Write Paths & gtsf:// Resolution [checkpoint: 22a53a2]

- [x] Task: `create_folder()` `ad57113`
    - [ ] Write Tests: `POST /api/files/_folder` body `{"folder_path", "name"}`, `FileEntry` response
    - [ ] Implement: `create_folder()`
- [x] Task: `upload()` (multipart) `7c6a07e`
    - [ ] Write Tests: `POST /api/files/_upload?o&path`, repeated `file` multipart fields with filename + guessed content type, no JSON content-type header, 300 s timeout, multiple files in one request
    - [ ] Implement: `upload()` with `contextlib.ExitStack`-managed handles and `mimetypes` guessing
- [x] Task: `resolve_gtsf_uri()` `41008c6`
    - [ ] Write Tests: accept/reject matrix — valid `gtsf://<id>`; rejects bad scheme, empty id, embedded `/`, uppercase scheme — all `ValueError` raised before any network call
    - [ ] Implement: strict parser delegating to `signed_url()`
- [x] Task: `download()` `1836889`
    - [ ] Write Tests: signed-url fetch then streamed GET to target path, parent dirs created, bytes written match, returns `FileEntry`
    - [ ] Implement: `download()` with `stream=True` + `iter_content`
- [x] Task: Conductor - User Manual Verification 'Phase 3: Write Paths & gtsf:// Resolution' (Protocol in workflow.md) `22a53a2`

## Phase 4: Facade, Cleanup & Companion Artifacts

- [x] Task: Facade integration `48043e6`
    - [ ] Write Tests: `AgencyClient.files()` returns an `AgencyFilesClient` sharing `token_supplier` and `base_url`
    - [ ] Implement: compose `AgencyFilesClient` in `client.py`, add `files()` accessor
- [x] Task: Drive-by cleanup — remove debug `print(result)` from `rules_client.py` `execute()` `85da857`
- [x] Task: Companion artifacts (Delegate Delivery Checklist) `5b9a968`
    - [ ] Write `examples/quick_files.py` — self-verifying lifecycle script (E2E-ready for the follow-up track)
    - [ ] Update README (Delegate Clients list + files usage snippet)
    - [ ] Update CLAUDE.md (register `files_client.py` / `files_dto.py` in the architecture section)
- [ ] Task: Static gates & coverage
    - [ ] Run `mypy agency_sdk/` (strict) — exit 0
    - [ ] Run `black --check agency_sdk/ examples/` — exit 0
    - [ ] Run `bandit -r agency_sdk/` — no findings
    - [ ] Run `pytest --cov=agency_sdk --cov-report=term-missing` — new modules >80%
- [ ] Task: Conductor - User Manual Verification 'Phase 4: Facade, Cleanup & Companion Artifacts' (Protocol in workflow.md)
