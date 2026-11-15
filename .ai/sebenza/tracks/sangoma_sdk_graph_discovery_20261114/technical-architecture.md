# Technical Architecture — Sangoma SDK knowledge-graph list & condition subgraph

Back to [design.md](./design.md).

**Minimal — out of scope for a full section.** No infrastructure, runtime, deployment or platform
change: the work is pure Python inside the existing `agency_sdk` package (Python ≥3.12, `requests`,
pydantic v2), using the base client's existing 30 s timeout and GET-only connection retry (no new
retry or backoff, per handover §4). Distribution is unchanged — the `v*`-tag PyPI pipeline — with a
PEP 440 pre-release (e.g. `X.Y.ZrcN`) for gts-sangoma-agent to pin. Both endpoints already exist in comand. The only environment need is
a **non-production comand** with the medical-ontology graph enabled, for the contract smoke test
(UC-04). No new dependencies; `tech-stack.md` is unaffected.
