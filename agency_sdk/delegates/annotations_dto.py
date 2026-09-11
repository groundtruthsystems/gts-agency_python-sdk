"""DTOs for the annotations API (snake_case, matching the API).

Mirrors the gts-comand annotation models (`crates/comand/src/model/annotation.rs`,
`service/annotation_service.rs`, verified at `8d64a64a`):

- A **batch** is the container annotators work through. It is created in ``DRAFT``
  with ``total_jobs = 0``; uploading a graph materialises one job per matching
  vertex and flips it to ``ACTIVE``.
- The server serialises every field, so nulls arrive explicitly rather than being
  omitted — each optional field therefore defaults to ``None`` and tolerates both.
- ``AnnotationBatchResponse`` is the single-batch read: the server flattens the
  batch's own fields alongside ``viewer_role``, so this model extends
  :class:`AnnotationBatch` rather than nesting it. The **list** read returns plain
  batches (no ``viewer_role``).
- ``CreateBatchResult`` / ``CreateSpecResult`` wrap the platform's standard command
  envelope ``{success, message, data}`` with ``data.id`` lifted to ``id`` — the
  ``_command`` endpoints do **not** return a bare ``{"id": ...}``.
- A **job** is one unit of that work: one vertex for an annotator to review. The
  list read and the single read return *different* server structs, so
  ``AnnotationJobSummary`` and ``AnnotationJob`` are separate models rather than one
  extending the other (job models verified at `90f95ad8`).
- A job's **transition ledger** (``JobTransitionEntry``,
  `model/annotation_workflow.rs`) is the approval evidence, read separately because
  it is append-only and unbounded. Its ``note`` is PHI-capable.
"""

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field

from agency_sdk.delegates.datasets_dto import Page

#: Batch types the server accepts. Graph batches are the rule-annotation path.
BATCH_TYPE_GRAPH = "graph"
BATCH_TYPE_DATASET = "dataset"

#: Server-side defaults for the graph upload's query parameters. The SDK omits
#: these params when the caller leaves them unset, so the server stays the single
#: source of truth; they are exposed for documentation and for callers that want
#: to be explicit.
DEFAULT_JOB_TYPE = "rule_validation"
DEFAULT_TARGET_CLASS = "rule"
DEFAULT_CONTEXT_HOPS = 1

#: Confidentiality levels a batch may carry. ``RESTRICTED`` gates the batch behind
#: explicit membership (the creator is seeded as its first admin).
LEVEL_INTERNAL = "INTERNAL"
LEVEL_RESTRICTED = "RESTRICTED"


class BatchStatus(IntEnum):
    """Lifecycle of an annotation batch (server ``BATCH_STATUS_*`` constants)."""

    DRAFT = 0
    ACTIVE = 1
    COMPLETED = 2
    ARCHIVED = 3


class SpecStatus(IntEnum):
    """Lifecycle of a job specification (server ``SPEC_STATUS_*`` constants)."""

    DRAFT = 0
    ACTIVE = 1
    ARCHIVED = 2


class AnnotationBatch(BaseModel):
    """An annotation batch as returned by the annotations API.

    ``graph_uri`` / ``graph_run_id`` / ``target_class`` / ``context_hops`` are the
    graph subtype's fields: they are hydrated by a join and stay ``None`` until a
    graph has been uploaded (and for dataset batches, always).
    """

    id: str
    organisation_id: int
    name: str
    description: str | None = None
    instructions: str | None = None
    batch_type: str
    graph_uri: str | None = None
    graph_run_id: str | None = None
    target_class: str | None = None
    context_hops: int | None = None
    total_jobs: int
    # Job counters differ by server generation, so all four are optional and one
    # model parses both. Older control planes send a single ``completed_jobs``;
    # gts-comand replaced it with the three below, because "done" and "done well"
    # are different questions that the one counter conflated.
    completed_jobs: int | None = None
    resolved_jobs: int | None = None
    accepted_jobs: int | None = None
    rejected_jobs: int | None = None
    status: int
    confidentiality_level: str
    audit_data: dict[str, Any] | None = None


class AnnotationBatchResponse(AnnotationBatch):
    """A single batch read, augmented with the caller's role on it.

    ``viewer_role`` is ``None`` for non-members, ``"admin"`` / ``"member"`` for an
    active membership.
    """

    viewer_role: str | None = None


class RejectedRow(BaseModel):
    """A row the server sent that this SDK could not parse.

    Reported rather than dropped. A list read that silently shrank would leave the
    caller believing it had seen everything, which is worse than either failing or
    complaining: the caller can handle a row it has been told about, and cannot
    handle one it never learns of.
    """

    #: Position in the page **as the server sent it** — not among the survivors, so
    #: it still lines up with a server-side log or a re-fetch of the same page.
    index: int
    #: The row verbatim, for logging or for a caller that wants to salvage it.
    raw: Any
    #: The validation failure, as pydantic reported it.
    error: str


class TolerantPage(BaseModel):
    """Base for a paged result that survives a malformed row.

    Every list read on this delegate validates its items one at a time: the
    well-formed rows come back in ``items``, and anything that failed is in
    ``rejected``, which is empty on a healthy page. One bad row used to raise for
    the whole page, making the good rows unreachable rather than merely unreported.

    A malformed ``page`` envelope still raises. That is a broken response rather than
    a bad row, and a page whose paging cannot be trusted is not worth handing back.

    **If you page, page on** :attr:`row_count`. Tolerating a bad row cost an
    invariant that was never written down: there used to be exactly two outcomes —
    every row parsed, so ``len(items)`` *was* the number of rows the server sent, or
    the construction raised and there was no result. There is now a third, and the
    usual "a short page is the last page" test silently assumes the old one::

        if len(items) < page_size:   # WRONG: a full page with two rejects
            break                    #        looks short, and the loop stops early

        if page.row_count < page_size:   # right: what the server actually sent
            break

    Reading it the first way truncates the walk, and a malformed row does not heal,
    so it truncates it again on every run. On a transition ledger that is not merely
    incomplete: a later row carrying ``prior_actor_conflict`` never gets read, so a
    separation-of-duties check that was failing closed begins failing **open**.
    """

    page: Page
    rejected: list[RejectedRow] = Field(default_factory=list)

    @property
    def row_count(self) -> int:
        """Rows the server sent on this page — parsed or not.

        ``len(items)`` counts only what parsed, so it is the wrong number to compare
        against the page size when deciding whether another page exists.
        """
        items: list[Any] = getattr(self, "items", [])
        return len(items) + len(self.rejected)


class AnnotationBatchesPagedResult(TolerantPage):
    items: list[AnnotationBatch]


class CommandResult(BaseModel):
    """The standard ``{success, message, data}`` envelope with ``data.id`` lifted."""

    success: bool
    message: str
    id: str


class CreateBatchResult(CommandResult):
    """Outcome of ``create_batch``: the new batch's id (batch starts in ``DRAFT``)."""


class CreateSpecResult(CommandResult):
    """Outcome of ``create_spec``: the new specification's id (created ``ACTIVE``)."""


class AnnotationSpec(BaseModel):
    """A job specification: the checklist a job type's jobs are seeded from.

    On upload the server looks the spec up by ``code`` == the upload's ``job_type``
    and seeds each job's ``checklist_state`` with ``{item_id: false}`` per checklist
    item. Without a matching spec the upload still succeeds and jobs get an empty
    checklist.
    """

    id: str
    organisation_id: int
    code: str
    name: str
    instructions: str | None = None
    checklist: Any = None
    status: int
    audit_data: dict[str, Any] | None = None


class AnnotationSpecsPagedResult(TolerantPage):
    items: list[AnnotationSpec]


class AnnotationWorkflow(BaseModel):
    """A workflow an organisation can bind to a batch, as listed by the API.

    A batch cannot receive jobs until a workflow is bound to it, so publishing
    starts by resolving one of these. ``target_batch_type`` says which batch type
    it governs (``"graph"`` / ``"dataset"``), and only a workflow with a
    ``current_published_version_id`` can be bound — the bind resolves the
    *published* version server-side.
    """

    id: str
    code: str
    name: str
    description: str | None = None
    target_batch_type: str
    is_system: bool
    status: str
    current_published_version_id: str | None = None
    draft_version_id: str | None = None


class AnnotationWorkflowsPagedResult(TolerantPage):
    items: list[AnnotationWorkflow]


class BindWorkflowResult(BaseModel):
    """Outcome of binding a workflow to a batch.

    ``jobs_regoverned`` is 0 for a batch that has no jobs yet — the normal case when
    binding right after ``create_batch`` — and non-zero when re-binding a batch whose
    jobs move to the new workflow.
    """

    success: bool
    message: str
    workflow_version_id: str
    jobs_regoverned: int


class PushGraphResult(BaseModel):
    """Outcome of the create → upload → read-back push.

    ``total_jobs`` and ``status`` are read back from the server (the upload
    endpoint's own response body is ``null``), so they reflect what the annotators
    will actually see.
    """

    batch_id: str
    total_jobs: int
    status: int
    batch: AnnotationBatchResponse


class AnnotationJobSummary(BaseModel):
    """A job as it appears in a **list** page: identity and pipeline position only.

    The heavy payloads — ``vertex_data``, ``connected_vertices``,
    ``connected_edges``, ``delta``, ``data``, ``checklist_state``,
    ``annotation_data`` — are deliberately absent. A list page never renders them,
    and the server's deferred join exists precisely to keep those wide rows off the
    page. Follow up with
    :meth:`~agency_sdk.delegates.annotations_client.AgencyAnnotationsClient.get_job`
    for any of them.

    ``vertex_bid`` / ``vertex_name`` / ``display`` are subtype labels and stay
    ``None`` for a dataset job. Clients render ``vertex_name``, then ``display``,
    then ``vertex_bid``.
    """

    id: str
    batch_id: str
    organisation_id: int
    job_type: str
    vertex_bid: str | None = None
    vertex_name: str | None = None
    display: str | None = None
    state_code: str
    workflow_version_id: str
    audit_data: dict[str, Any] | None = None


class AnnotationJob(BaseModel):
    """A job as the **single** read returns it: the full row, payloads included.

    Deliberately NOT a subclass of :class:`AnnotationJobSummary`. The two are
    distinct server structs that happen to overlap, and modelling the full row as an
    extension of the list row would claim a substitutability the API does not
    promise — a summary would then satisfy a type annotation asking for the
    payloads. (Contrast :class:`AnnotationBatchResponse`, which genuinely *is* a
    batch plus one field.)

    Four fields are **opaque blobs** and typed ``Any`` on purpose:
    ``vertex_data`` / ``connected_vertices`` / ``connected_edges`` are the graph
    context the upload attached, and ``delta`` / ``checklist_state`` /
    ``annotation_data`` are written by whichever front-end saved the job. comand
    never parses them — the update arm does whole-value column replacement — and
    two different apps write different structures into the same columns. Validating
    them here would break on a shape change the server itself tolerates, so the SDK
    carries them through untouched and leaves interpretation to the caller.
    """

    id: str
    batch_id: str
    organisation_id: int
    job_type: str
    # Graph subtype (annotation_graph_job).
    vertex_bid: str | None = None
    vertex_name: str | None = None
    vertex_data: Any = None
    connected_vertices: Any = None
    connected_edges: Any = None
    delta: Any = None
    # Dataset subtype (annotation_dataset_job); ``display`` is resolved for both.
    display: str | None = None
    data: Any = None
    # Shared review fields (base table).
    checklist_state: Any = None
    annotation_data: Any = None
    #: A state declared by the job's governing workflow **version**, not by the SDK
    #: — which is why this is a plain string and not an enum. The dispositions a
    #: publisher acts on are ``"completed"`` (accepted) and ``"skipped"``
    #: (rejected); the full set belongs to the workflow.
    state_code: str
    #: Which version governs this job **right now**. Bindings move, so a transition's
    #: own stamped ``workflow_version_id`` may differ — see
    #: :class:`JobTransitionEntry`.
    workflow_version_id: str
    #: Bumped by every content write. Snapshot it at read and compare after
    #: assembling: a change means the job was edited underneath you, so work built
    #: on the earlier view must be discarded rather than published. Approved content
    #: is not immutable server-side, which is what makes this check necessary.
    revision: int
    audit_data: dict[str, Any] | None = None


class AnnotationJobsPagedResult(TolerantPage):
    items: list[AnnotationJobSummary]


class JobTransitionEntry(BaseModel):
    """One row of a job's transition ledger — the approval evidence.

    Append-only and unbounded, which is why it is a separate read from the job. It
    is what makes a completed job attributable: who fired which transition, acting
    as what role, and under which policy.

    ``prior_actor_conflict`` flags a transition fired by someone who had already
    acted on this job, i.e. the distinct-actor guard was not satisfied. A consumer
    building on approval evidence should treat it as a stop signal rather than a
    warning.
    """

    id: int
    job_id: str
    batch_id: str
    organisation_id: int
    #: The version **in force when this transition fired**, not the job's current
    #: one. Bindings move, so only this stamped copy makes a past transition
    #: attributable to the policy that permitted it.
    workflow_version_id: str
    from_state: str | None = None
    to_state: str
    transition_code: str
    actor_type: str
    actor_user_id: int | None = None
    acting_as_role: str
    note: str | None = Field(
        default=None,
        description=(
            "Free-text reason attached to the transition. PHI-capable: it is written "
            "by clinicians and may contain patient information. Never log it, trace "
            "it, or copy it into telemetry — comand deliberately excludes it from its "
            "own access log, and re-exporting it here would defeat that."
        ),
    )
    prior_actor_conflict: bool
    occurred_on: str


class JobTransitionsPagedResult(TolerantPage):
    items: list[JobTransitionEntry]
