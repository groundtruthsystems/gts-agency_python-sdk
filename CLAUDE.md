# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, files, ontologies, prompts, and rules APIs.

- **Python:** >=3.12
- **Key deps:** requests, pydantic (v2), pyjwt

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Type checking (strict mode)
mypy agency_sdk/

# Formatting (120 char line length)
black agency_sdk/

# Build package
python -m build

# Run an example
python examples/quick_clone_dataset.py

# Run tests (offline; requests is stubbed via monkeypatch)
pytest
```

## Architecture

**Entry point:** `AgencyClient` (in `client.py`) is a facade that composes six delegate clients, all sharing a `CredentialsSupplier` for OAuth2 client-credentials auth with automatic token caching/refresh.

**Delegate pattern:** Each API domain has a client + DTO module pair in `delegates/`:
- `datasets_client.py` / `datasets_dto.py` — CRUD + filesystem traversal + clone
- `datasource_client.py` / `datasource_dto.py` — datasource + table introspection
- `files_client.py` / `files_dto.py` — tenant file storage: list/upload/folders/delete, signed URLs, `gtsf://` resolution, streamed download (see `docs/files_storage_flows.md`)
- `ontology_client.py` / `ontology_dto.py` — export (multiple formats) + entity-datasource mappings
- `prompts_client.py` + `domain.py` — prompt CRUD via command pattern (`POST /_command`)
- `rules_client.py` / `rules_dto.py` — rule listing, detail, execution + execution history

**DTOs:** All models use Pydantic v2 `BaseModel`. Datasource, ontology, and rules DTOs use `ConfigDict(alias_generator=_to_camel, populate_by_name=True)` for camelCase JSON mapping. Prompt/dataset/files DTOs use snake_case matching the API.

**Shared type:** `Page` is defined in `datasets_dto.py` and imported by other DTO modules for pagination.

**Tests:** Offline suite in `agency_sdk/test/`; `conftest.py` stubs `requests` via monkeypatch so no test touches the network. mypy is relaxed for `agency_sdk.test.*` per pyproject overrides.

## Conventions

- Use `dict | None` syntax (PEP 604), not `Optional[Dict]` — Python 3.12+ only
- Pydantic v2 API: `model_dump(mode="json")`, `ConfigDict`, `Field`
- HTTP errors propagate via `response.raise_for_status()` — no custom exception wrapping
- API query params use abbreviations: `o` (org), `s` (size), `p` (page), `v` (version)
- Line length: 120 characters (black config)
- mypy strict mode for all production code; tests excluded

## CI/CD

`.github/workflows/publish.yaml` — triggered by `v*` tags. Builds, publishes to PyPI via trusted publisher (OIDC), notifies Slack.