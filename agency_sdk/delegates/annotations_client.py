"""Client for the annotations API (``/api/annotations``, ``/api/annotation-specs``).

Publishes a knowledge graph as work for human annotators. There is no single
"publish" endpoint: a batch is **created** in ``DRAFT`` and the graph **upload**
is what materialises the jobs and flips the batch to ``ACTIVE``. The graph the
upload accepts is the same ``create.graph`` payload agents already build for the
ontology sandbox (``run_id`` / ``vertices`` / ``edges``), so a caller that has one
can push it as-is — see :meth:`AgencyAnnotationsClient.push_graph`, the one-call
convenience over the whole flow.

Contract verified against gts-comand ``8d64a64a``
(``crates/comand/src/handler/annotations.rs``, ``service/annotation_service.rs``).
Every handler here requires a principal with a local user id **and** organisation
write permission on ``Resource::Annotations``.
"""

from __future__ import annotations

from agency_sdk.delegates.base_client import BaseDelegateClient

#: The job specifications live under their own root, NOT under ``api_path``.
SPECS_PATH = "/api/annotation-specs"


class AgencyAnnotationsClient(BaseDelegateClient):
    api_path = "/api/annotations"
