"""Client for the annotations API.

Covers ``/api/annotations`` plus two sibling roots the same capability needs:
``/api/annotation-specs`` (job checklists) and ``/api/annotation-workflows`` (the
review flows a batch is bound to).

Publishes a knowledge graph as work for human annotators. There is no single
"publish" endpoint. A batch is **created** in ``DRAFT``; a workflow must then be
**bound** to it, because the server resolves every job's governing workflow from the
batch and refuses the insert when there is none; and the graph **upload** is what
materialises the jobs and flips the batch to ``ACTIVE``. The graph the upload accepts
is the same ``create.graph`` payload agents already build for the ontology sandbox
(``run_id`` / ``vertices`` / ``edges``), so a caller that has one can push it as-is —
see :meth:`AgencyAnnotationsClient.push_graph`, the one-call convenience over the
whole flow.

Contract verified against gts-comand ``eda4f9ca``
(``crates/comand/src/handler/annotations.rs``, ``service/annotation_service.rs``,
``service/annotation_binding_service.rs``, and the ``annotation_job_before_insert``
trigger in ``data/95__annotation_state_constraints.sql``). Every handler here requires
a principal with a local user id **and** organisation write permission on
``Resource::Annotations``; ``bind_workflow`` additionally gates on workflow-execute.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.annotations_dto import (
    BATCH_TYPE_GRAPH,
    AnnotationBatchesPagedResult,
    AnnotationBatchResponse,
    AnnotationSpec,
    AnnotationSpecsPagedResult,
    AnnotationWorkflow,
    AnnotationWorkflowsPagedResult,
    BindWorkflowResult,
    CreateBatchResult,
    CreateSpecResult,
    PushGraphResult,
)
from agency_sdk.delegates.base_client import BaseDelegateClient

#: The job specifications live under their own root, NOT under ``api_path``.
SPECS_PATH = "/api/annotation-specs"

#: The workflows an organisation can bind to a batch — also their own root.
WORKFLOWS_PATH = "/api/annotation-workflows"

#: ``job_type`` value that binds a workflow as the batch's default, covering every
#: job type. The server's insert trigger resolves ``(batch_id, job_type)`` first and
#: falls back to this, so a single default binding is enough for any upload.
WILDCARD_JOB_TYPE = "*"

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


class _AnnotationWorkflowsEndpoint(BaseDelegateClient):
    """The annotation workflows, which hang off their own API root.

    Module-private for the same reason as :class:`_AnnotationSpecsEndpoint`: it keeps
    the workflows on the shared request plumbing without exposing a second delegate
    for what is one capability from the caller's point of view.
    """

    api_path = WORKFLOWS_PATH


class _AnnotationSpecsEndpoint(BaseDelegateClient):
    """The job specifications, which hang off their own API root.

    Module-private: the specs are part of the annotations surface and are reached
    through :class:`AgencyAnnotationsClient`. Modelling them as a sibling delegate
    keeps them on the shared request plumbing (bearer, timeout, read retries)
    instead of hand-rolling a second one for a different path prefix.
    """

    api_path = SPECS_PATH


class AgencyAnnotationsClient(BaseDelegateClient):
    api_path = "/api/annotations"

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        super().__init__(token_supplier=token_supplier, base_url=base_url)
        self._specs = _AnnotationSpecsEndpoint(token_supplier=token_supplier, base_url=base_url)
        self._workflows = _AnnotationWorkflowsEndpoint(token_supplier=token_supplier, base_url=base_url)

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

        Transport: this posts directly rather than through :meth:`_request`, whose
        JSON content type would corrupt a multipart body. No retry behaviour is lost
        by doing so — the base client only auto-retries reads, so a ``POST`` gets a
        single attempt either way. That is the right policy here: a connection reset
        can arrive *after* the server committed the upload, and re-sending would meet
        the now-ACTIVE batch with a 400 rather than land twice.

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

    def list_workflows(self, organisation_id: int, *, page: int = 0, size: int = 50) -> AnnotationWorkflowsPagedResult:
        """List the workflows an organisation can bind to a batch (paged).

        Used to resolve a workflow id rather than hardcode one: the system workflows
        are seeded **per organisation**, so ``sys-wf-graph-2`` is org 2's id and
        nobody else's.
        """
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        return AnnotationWorkflowsPagedResult(**self._workflows._make_request("GET", "", params=params))

    def bind_workflow(
        self,
        organisation_id: int,
        batch_id: str,
        *,
        workflow_id: str,
        job_type: str = WILDCARD_JOB_TYPE,
        rebind_reason: str | None = None,
    ) -> BindWorkflowResult:
        """Bind a workflow to a batch, without which the batch can hold no jobs.

        A batch is created with **no** binding, and the server's insert trigger
        resolves every job's governing workflow from the batch's bindings —
        ``(batch_id, job_type)`` first, then ``(batch_id, "*")`` — refusing the insert
        when neither exists. So this must run between :meth:`create_batch` and
        :meth:`upload_graph`; :meth:`push_graph` does it for you.

        Binding is a deliberate, separately-permissioned step (the server gates it on
        workflow-execute rather than batch admin) because which workflow governs a
        batch is a policy choice — an organisation can have several per batch type.

        Args:
            organisation_id: The organisation ID.
            batch_id: The batch to bind.
            workflow_id: The workflow, e.g. from :meth:`list_workflows`. The server
                resolves it to its **published** version, so an unpublished workflow
                cannot be bound.
            job_type: The job type this binding governs; ``"*"`` (default) makes it
                the batch default covering every type.
            rebind_reason: Required by the server when re-binding a job type that
                already has accepted work, or when the incoming version disables
                separation of duties.
        """
        payload: dict[str, Any] = {"job_type": job_type, "workflow_id": workflow_id}
        if rebind_reason is not None:
            payload["rebind_reason"] = rebind_reason
        body = self._make_request(
            "POST",
            f"/{batch_id}/_command",
            data={"command": "bind_workflow", "organisation": organisation_id, "payload": payload},
        )
        data = body.get("data") or {}
        return BindWorkflowResult(
            success=body["success"],
            message=body["message"],
            workflow_version_id=data.get("workflow_version_id", ""),
            jobs_regoverned=data.get("jobs_regoverned", 0),
        )

    def _resolve_workflow_id(self, organisation_id: int, batch_type: str) -> str | None:
        """Pick a bindable workflow for ``batch_type``, or ``None`` on a pre-workflow server.

        Bindable means: governs this batch type, and has a published version — the
        bind resolves the published version server-side, so a draft-only workflow
        cannot be used. System workflows win, since those are the seeded defaults;
        beyond that the server's order decides. Ambiguity is deliberately not an
        error: an organisation with two graph workflows would otherwise be unable to
        push at all, and such a caller can pass ``workflow_id`` explicitly.

        A ``404`` means a control plane older than the workflow model, where no
        binding exists or is needed; that returns ``None`` so the caller skips the
        bind. Every other error propagates.
        """
        try:
            page = self.list_workflows(organisation_id, size=100)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                return None
            raise
        bindable = [
            workflow
            for workflow in page.items
            if workflow.target_batch_type == batch_type and workflow.current_published_version_id
        ]
        if not bindable:
            offered = ", ".join(f"{w.code}({w.target_batch_type})" for w in page.items) or "none"
            raise ValueError(
                f"no bindable annotation workflow for batch_type {batch_type!r} in organisation "
                f"{organisation_id}: a batch cannot hold jobs until a workflow is bound to it. "
                f"Workflows offered: {offered}. Publish one, or pass workflow_id explicitly."
            )
        return sorted(bindable, key=lambda w: not w.is_system)[0].id

    def push_graph(
        self,
        organisation_id: int,
        *,
        name: str,
        graph: Mapping[str, Any] | None = None,
        file_path: str | Path | None = None,
        description: str | None = None,
        instructions: str | None = None,
        confidentiality_level: str | None = None,
        job_type: str | None = None,
        target_class: str | None = None,
        hops: int | None = None,
        filename: str | None = None,
        workflow_id: str | None = None,
        rebind_reason: str | None = None,
    ) -> PushGraphResult:
        """Publish a graph as annotation jobs: create → bind → upload → read back.

        The whole flow an agent needs at the end of its pipeline. Arguments are the
        union of :meth:`create_batch`'s and :meth:`upload_graph`'s; the returned
        ``total_jobs`` and ``status`` come from the read-back, so they are what the
        annotators will actually see.

        The **bind** leg is not optional: a batch is created with no workflow, and the
        server refuses to insert a job into an unbound batch — which surfaces as an
        opaque 500 from the upload. By default the workflow is resolved from
        :meth:`list_workflows` (published, matching the batch type, system first),
        never hardcoded, since the seeded ids differ per organisation. Pass
        ``workflow_id`` to choose one yourself and skip the lookup; against a control
        plane predating workflows the lookup 404s and the bind is skipped.

        The graph source is validated before the batch is created, so a malformed
        call cannot leave anything behind. An upload that the server rejects (no
        matching vertices, oversized body, …) is a different matter: the batch has
        already been created, so the ``HTTPError`` propagates and an **empty DRAFT
        batch stays server-side**. It holds no jobs and is findable via
        :meth:`list_batches`; the SDK does not archive it on the caller's behalf.

        Raises:
            ValueError: If not exactly one of ``graph`` / ``file_path`` is given, or
                if the create response carries no batch id.
            requests.HTTPError: From any of the three legs.
        """
        if (graph is None) == (file_path is None):
            raise ValueError("pass exactly one of graph or file_path")
        # Resolve BEFORE creating anything: a batch with no bindable workflow is a
        # batch that can never hold jobs, and failing after create_batch would strand
        # exactly the empty DRAFT batch this method is trying not to leave behind.
        resolved_workflow_id = workflow_id or self._resolve_workflow_id(organisation_id, BATCH_TYPE_GRAPH)
        created = self.create_batch(
            organisation_id,
            name=name,
            description=description,
            instructions=instructions,
            confidentiality_level=confidentiality_level,
        )
        if resolved_workflow_id is not None:
            self.bind_workflow(
                organisation_id,
                created.id,
                workflow_id=resolved_workflow_id,
                rebind_reason=rebind_reason,
            )
        self.upload_graph(
            organisation_id,
            created.id,
            graph=graph,
            file_path=file_path,
            job_type=job_type,
            target_class=target_class,
            hops=hops,
            filename=filename,
        )
        batch = self.get_batch(organisation_id, created.id)
        return PushGraphResult(batch_id=created.id, total_jobs=batch.total_jobs, status=batch.status, batch=batch)

    def get_batch(self, organisation_id: int, batch_id: str) -> AnnotationBatchResponse:
        """Read one batch, including the caller's ``viewer_role`` on it.

        This is how the job count is obtained: a batch stays ``DRAFT`` with
        ``total_jobs = 0`` until a graph is uploaded, after which it is ``ACTIVE``
        with ``total_jobs`` set and the graph subtype fields hydrated. ``status`` is
        an int — compare it against
        :class:`~agency_sdk.delegates.annotations_dto.BatchStatus`.
        """
        params = {"o": str(organisation_id)}
        return AnnotationBatchResponse(**self._make_request("GET", f"/{batch_id}", params=params))

    def list_batches(
        self,
        organisation_id: int,
        *,
        page: int = 0,
        size: int = 50,
        batch_type: str | None = None,
        view: str | None = None,
    ) -> AnnotationBatchesPagedResult:
        """List the org's batches (paged), e.g. to resolve a batch NAME → id.

        Unlike :meth:`get_batch`, the items carry no ``viewer_role``. Name matching
        is the caller's job; this returns the raw page.

        Args:
            organisation_id: The organisation ID.
            page: Zero-indexed page number.
            size: Page size (the server's own default is 10).
            batch_type: Optional ``"graph"`` / ``"dataset"`` filter.
            view: Optional server-side view filter.
        """
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        if batch_type is not None:
            params["batch_type"] = batch_type
        if view is not None:
            params["view"] = view
        return AnnotationBatchesPagedResult(**self._make_request("GET", "", params=params))

    def create_spec(
        self,
        organisation_id: int,
        *,
        code: str,
        name: str,
        checklist: Any,
        instructions: str | None = None,
    ) -> CreateSpecResult:
        """Create a job specification — the checklist a job type's jobs start from.

        On upload the server seeds each job's ``checklist_state`` with
        ``{item_id: false}`` for every checklist item of the spec whose ``code``
        equals the upload's ``job_type``. Seed the spec **before** pushing a graph;
        without one the upload still succeeds and the jobs reach annotators with an
        empty checklist. The spec is created ``ACTIVE``.

        Nothing enforces ``code`` uniqueness per organisation and the lookup takes
        the first match, so check with :meth:`get_spec` before creating.

        Args:
            organisation_id: The organisation ID.
            code: The job type this spec applies to (e.g. ``"rule_validation"``).
            name: Human-readable specification name.
            checklist: The checklist, a JSON array whose items each carry an ``id``.
            instructions: Optional instructions shown to the annotator.

        Raises:
            ValueError: If the server's command envelope carries no specification id.
        """
        payload: dict[str, Any] = {"code": code, "name": name, "checklist": checklist}
        if instructions is not None:
            payload["instructions"] = instructions
        body = self._specs._make_request(
            "POST", "/_command", data={"command": "create", "organisation": organisation_id, "payload": payload}
        )
        return CreateSpecResult(success=body["success"], message=body["message"], id=_command_id(body, "specification"))

    def get_spec(self, organisation_id: int, code: str) -> AnnotationSpec:
        """Read one job specification **by its code**.

        Note the path segment is the spec's ``code`` (e.g. ``"rule_validation"``),
        not its UUID, even though the server's own route names it ``{id}`` — it is
        resolved with a by-code lookup.

        Raises:
            requests.HTTPError: 404 when the organisation has no spec with that code.
        """
        return AnnotationSpec(**self._specs._make_request("GET", f"/{code}", params={"o": str(organisation_id)}))

    def list_specs(self, organisation_id: int, *, page: int = 0, size: int = 50) -> AnnotationSpecsPagedResult:
        """List the org's job specifications (paged; the server's own default size is 10)."""
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        return AnnotationSpecsPagedResult(**self._specs._make_request("GET", "", params=params))
