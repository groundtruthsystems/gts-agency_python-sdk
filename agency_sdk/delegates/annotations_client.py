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

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

from agency_sdk.delegates.annotations_dto import BATCH_TYPE_GRAPH, CreateBatchResult
from agency_sdk.delegates.base_client import BaseDelegateClient

#: The job specifications live under their own root, NOT under ``api_path``.
SPECS_PATH = "/api/annotation-specs"

#: Upload bodies are assembled in memory and the server caps the request body at
#: 50 MiB; a graph larger than this is rejected before it reaches the handler.
UPLOAD_TIMEOUT_SECONDS = 300


def _command_id(body: Mapping[str, Any], subject: str) -> str:
    """Lift ``data.id`` out of the standard ``{success, message, data}`` envelope.

    A 2xx without an id means the server accepted the command but told us nothing
    usable, so the caller cannot continue the flow — surface that as a ``ValueError``
    rather than returning a half-built result.
    """
    data = body.get("data")
    identifier = data.get("id") if isinstance(data, Mapping) else None
    if not isinstance(identifier, str) or not identifier:
        raise ValueError(f"server returned no {subject} id: {body!r}")
    return identifier


class AgencyAnnotationsClient(BaseDelegateClient):
    api_path = "/api/annotations"

    def create_batch(
        self,
        organisation_id: int,
        *,
        name: str,
        description: str | None = None,
        instructions: str | None = None,
        batch_type: str = BATCH_TYPE_GRAPH,
        confidentiality_level: str | None = None,
    ) -> CreateBatchResult:
        """Create an annotation batch. It starts in ``DRAFT`` with no jobs.

        Jobs are materialised by :meth:`upload_graph`, which also flips the batch to
        ``ACTIVE`` — a batch on its own is an empty container.

        Args:
            organisation_id: The organisation ID.
            name: Batch name, as shown to annotators.
            description: Optional free-text description.
            instructions: Optional annotation instructions, shown in the screen's
                title bar.
            batch_type: ``"graph"`` (default, the rule-annotation path) or
                ``"dataset"``.
            confidentiality_level: ``"INTERNAL"`` (server default) or
                ``"RESTRICTED"``, which gates the batch behind explicit membership
                and seeds the caller as its first admin. Unknown levels are
                rejected server-side with a 400.

        Raises:
            ValueError: If the server's command envelope carries no batch id.
        """
        payload: dict[str, Any] = {"name": name, "batch_type": batch_type}
        if description is not None:
            payload["description"] = description
        if instructions is not None:
            payload["instructions"] = instructions
        if confidentiality_level is not None:
            payload["confidentiality_level"] = confidentiality_level
        body = self._make_request(
            "POST", "/_command", data={"command": "create", "organisation": organisation_id, "payload": payload}
        )
        return CreateBatchResult(success=body["success"], message=body["message"], id=_command_id(body, "batch"))

    def upload_graph(
        self,
        organisation_id: int,
        batch_id: str,
        *,
        graph: Mapping[str, Any] | None = None,
        file_path: str | Path | None = None,
        job_type: str | None = None,
        target_class: str | None = None,
        hops: int | None = None,
        filename: str | None = None,
    ) -> None:
        """Upload a graph to a DRAFT batch, materialising one job per target vertex.

        The server creates a job for every vertex whose ``class`` equals
        ``target_class``, attaches each one's ``hops``-hop undirected neighbourhood
        as context, seeds its checklist from the specification whose ``code`` matches
        ``job_type`` (empty when there is none — see :meth:`create_spec`), stores the
        raw file, and flips the batch to ``ACTIVE`` with ``total_jobs`` set.

        The graph is the same ``{run_id, vertices, edges}`` payload agents send to
        the ontology sandbox, so a sandbox graph can be pushed unchanged.

        Nothing is returned: the endpoint's response body is ``null``. Read the job
        count back with :meth:`get_batch` (or use :meth:`push_graph`, which does).

        Memory: ``requests`` assembles the whole multipart body in memory, and the
        server caps the request body at 50 MiB.

        Args:
            organisation_id: The organisation ID.
            batch_id: The DRAFT batch to fill.
            graph: The graph as a mapping. Mutually exclusive with ``file_path``.
            file_path: A local ``.json`` graph file. Mutually exclusive with ``graph``.
            job_type: Job type to create (server default ``"rule_validation"``).
            target_class: Vertex class to turn into jobs (server default ``"rule"``).
            hops: Neighbourhood hops attached as context (server default ``1``).
            filename: Multipart filename. Defaults to the source file's name, or
                ``"graph.json"`` for an in-memory graph.

        Raises:
            ValueError: If not exactly one of ``graph`` / ``file_path`` is given;
                raised before any network call.
            requests.HTTPError: 400 if the batch is not in DRAFT, the JSON does not
                parse, ``vertices`` is missing, or no vertex matches ``target_class``.
        """
        if (graph is None) == (file_path is None):
            raise ValueError("pass exactly one of graph or file_path")
        if graph is not None:
            body = json.dumps(graph).encode()
            upload_name = filename or "graph.json"
        else:
            source = Path(file_path)  # type: ignore[arg-type]  # exactly-one check above
            body = source.read_bytes()
            upload_name = filename or source.name
        params = {"o": str(organisation_id)}
        if job_type is not None:
            params["job_type"] = job_type
        if target_class is not None:
            params["target_class"] = target_class
        if hops is not None:
            params["hops"] = str(hops)
        response = requests.post(
            f"{self.base_url}{self.api_path}/{batch_id}/upload",
            headers={"Authorization": f"Bearer {self.token_supplier.bearer_token()}"},
            params=params,
            files={"file": (upload_name, body, "application/json")},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
