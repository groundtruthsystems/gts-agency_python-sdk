# Technical Architecture — paginated knowledge-graph list (SDK)

Part of [design.md](./design.md).

**Minimal.** No runtime, infrastructure, deployment topology or platform change: the SDK stays a
pure-Python library over `requests`, and the call keeps the base client's retry and timeout. The one
technical constraint is **release sequencing**: this SDK cannot parse the old `{items}` body and older
SDKs cannot parse the new one, so the breaking version is published only after the paginated comand API
is deployed (handover §5.6), and implementation starts only after the gts-agency OpenAPI is merged and
checked against handover §2 (§5.1). Performance: the server has no maximum `size`; the SDK adds none
(NFR-01), so payload size is the caller's choice. Versioning follows the repository's existing
`pyproject.toml` convention (see Open Questions in design.md).
