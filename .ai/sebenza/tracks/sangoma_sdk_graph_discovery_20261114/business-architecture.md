# Business Architecture — Sangoma SDK knowledge-graph list & condition subgraph

Back to [design.md](./design.md).

**Minimal — out of scope for a full section.** This track adds two client-side operations to a
library; it changes no business process, stakeholder role or rule. The business capability (staged,
ontology-governed condition knowledge) and its rules — mode selection, approved sources, provenance,
no verbatim extracts — are owned by gts-sangoma-agent and the solution design, not by the SDK. The
only business-relevant guarantee the SDK carries is *fidelity*. `list` reports the catalogue as comand
does. `condition_subgraph` raises `ConditionNotFound` only on a real 404, so the agent never picks
*create* by mistake and duplicates a graph. Both are specified in the application and security
documents.
