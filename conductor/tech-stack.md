# Tech Stack — GTS Agency Python SDK

## Language

- **Python ≥ 3.12** — required minimum; the codebase uses PEP 604 union syntax
  (`X | None`) exclusively. `Optional[X]` / `Dict[...]` style annotations are not
  used.

## Runtime Dependencies

| Dependency | Version | Role |
|---|---|---|
| `requests` | ≥ 2.32.0 | Synchronous HTTP client for all API calls |
| `pydantic` | ≥ 2.9.2 | DTO models, validation, JSON (de)serialisation |
| `pyjwt` | ≥ 2.3.0, < 3.0.0 | Decoding cached JWTs to check `exp` (no signature verification) |

Pydantic v2 API only: `model_dump(mode="json")`, `ConfigDict`, `Field`.

## Development Tooling

| Tool | Version | Role |
|---|---|---|
| `mypy` | 1.4.1 | Type checking, **strict mode** for all production code; `agency_sdk.test.*` relaxed |
| `black` | 23.12.1 | Formatting, line length **120** |
| `pytest` | 7.4.0 | Test runner; offline suite in `agency_sdk/test/` stubs `requests` via monkeypatch |
| `pytest-cov` | 4.1.0 | Coverage measurement for the >80% workflow gate (added 2026-06-10, files_client track) |
| `pre-commit` | 3.8.0 | Git hook management |

## Build & Distribution

- **Build backend:** setuptools via `pyproject.toml` (`python -m build`).
- **Publishing:** GitHub Actions workflow (`.github/workflows/publish.yaml`)
  triggered by `v*` tags; publishes to PyPI through OIDC trusted publishing.
- **CI security gate:** `bandit -r agency_sdk/` runs as a required job before publish.

## Architecture

- **Pattern:** single-package client library. `AgencyClient` (`client.py`) is a
  facade composing per-domain delegate clients; each domain is a client + DTO module
  pair under `agency_sdk/delegates/`.
- **Authentication:** shared `CredentialsSupplier` (`credentials.py`) implementing
  OAuth2 client-credentials with in-memory token caching and expiry-based refresh.
- **HTTP conventions:** every delegate owns a `_make_request` helper; errors
  propagate via `raise_for_status()`; 30 s default timeout; query parameter
  abbreviations `o` (org), `s` (size), `p` (page), `v` (version).
- **DTO conventions:** Pydantic v2 `BaseModel` throughout. Datasource/ontology/rules
  DTOs map camelCase JSON via `alias_generator=_to_camel`; dataset/prompt/files DTOs
  use snake_case matching the API. Shared `Page` pagination type lives in
  `datasets_dto.py`.
