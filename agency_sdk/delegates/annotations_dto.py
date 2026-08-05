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
"""

from enum import IntEnum
from typing import Any

from pydantic import BaseModel

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
    completed_jobs: int
    status: int
    confidentiality_level: str
    audit_data: dict[str, Any] | None = None


class AnnotationBatchResponse(AnnotationBatch):
    """A single batch read, augmented with the caller's role on it.

    ``viewer_role`` is ``None`` for non-members, ``"admin"`` / ``"member"`` for an
    active membership.
    """

    viewer_role: str | None = None


class AnnotationBatchesPagedResult(BaseModel):
    page: Page
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


class AnnotationSpecsPagedResult(BaseModel):
    page: Page
    items: list[AnnotationSpec]


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
