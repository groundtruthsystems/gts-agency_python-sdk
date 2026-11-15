"""Knowledge-graph reads through comand: the org's catalogue and a condition's known subgraph.

Both calls go through comand only. ``condition_subgraph`` reaches the medical ontology service's
``/api/models`` endpoints via comand's ``/api/knowledge-graphs/medical-ontology/{*path}`` proxy, which
injects the service's API key, strips ``o`` and passes the upstream status and body through. The SDK
never calls the ontology service directly.

``condition_subgraph`` is the *create mode* signal for the research agent, so it fails closed:

- Only a resolve 404 whose body is exactly the ontology service's ``{"detail": "not found"}`` raises
  :class:`ConditionNotFound`. A 404 with any other body (a comand or gateway misroute), any other
  status, any walk failure and any transport error propagates unchanged as a ``requests`` exception.
- Resolve comes first because ``_graph`` answers an unknown ``bid`` with an empty 200.
- The code and the server's ``bid`` are both checked with ``re.fullmatch`` before either becomes a path
  segment.

Retry, backoff and the decision to create stay with the caller. Reads keep the base client's default
connection retry (3 attempts); read timeouts and HTTP statuses are never retried.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.graphs_dto import (
    CONDITION_CLASS,
    ConditionClassMismatch,
    ConditionNotFound,
    ConditionSubgraph,
    KnowledgeGraphList,
    ResolvedEntity,
)

__all__ = [
    "AgencyGraphsClient",
    "ConditionClassMismatch",
    "ConditionNotFound",
]

#: ``C`` + 7 digits is a UMLS CUI; ``B`` + 7 digits is an authored id. ``fullmatch`` refuses a trailing newline.
_CODE_RE = re.compile(r"[CB][0-9]{7}")

#: The ontology service's not-found body (``error.rs`` ``ErrorBody``). Nothing else means *not found*.
_MOS_NOT_FOUND: dict[str, Any] = {"detail": "not found"}

#: Path under ``api_path`` for comand's medical-ontology proxy into ``/api/models``.
_MODELS = "/medical-ontology/api/models"

Direction = Literal["out", "in", "any"]
_DIRECTIONS = frozenset({"out", "in", "any"})
_MIN_DEPTH, _MAX_DEPTH = 1, 6


class AgencyGraphsClient(BaseDelegateClient):
    api_path = "/api/knowledge-graphs"

    def list(self, organisation_id: int) -> KnowledgeGraphList:
        """Return the org's knowledge-graph catalogue (``GET /api/knowledge-graphs?o=``). Not paged."""
        body = self._make_request("GET", "", params={"o": str(organisation_id)})
        catalogue: KnowledgeGraphList = KnowledgeGraphList.model_validate(body)
        return catalogue

    def condition_subgraph(
        self,
        organisation_id: int,
        code: str,
        *,
        depth: int = 1,
        direction: Direction = "any",
    ) -> ConditionSubgraph:
        """Return the known subgraph of the condition identified by ``code``.

        Args:
            organisation_id: The org whose knowledge graph is read.
            code: ``C`` + 7 digits (UMLS CUI) or ``B`` + 7 digits (authored id).
            depth: Walk depth, 1-6.
            direction: ``"out"``, ``"in"`` or ``"any"``. The default ``"any"`` includes inbound edges
                such as Medication -treats-> Condition.

        Raises:
            ValueError: Invalid ``code``, ``depth`` or ``direction``; no request is sent.
            ConditionNotFound: The knowledge graph has no entity for ``code`` (create mode).
            ConditionClassMismatch: ``code`` resolves to an entity that is not a condition (stop).
            pydantic.ValidationError: A malformed resolve or walk body.
            requests.RequestException: Any other HTTP or transport failure. Never *not found*.

        Known limit: the ontology service matches only an entity's first code, so a condition stored
        under a secondary code reads as not found. Confirming before creating is the caller's job.
        """
        check_subgraph_args(code, depth, direction)

        org = str(organisation_id)
        root_body = self._resolve(org, code)
        entity = ResolvedEntity.model_validate(root_body)
        if entity.entity_class != CONDITION_CLASS:
            raise ConditionClassMismatch(entity.entity_class)

        walk = self._request(
            "GET",
            f"{_MODELS}/{CONDITION_CLASS}/{entity.bid}/_graph",
            params={"o": org, "depth": str(depth), "direction": direction},
        ).json()
        walk_fields = {k: walk[k] for k in ("vertices", "edges") if k in walk} if isinstance(walk, dict) else {}
        subgraph: ConditionSubgraph = ConditionSubgraph.model_validate({"root": root_body, **walk_fields})
        return subgraph

    def _resolve(self, org: str, code: str) -> Any:
        """Resolve ``code`` to its entity document, mapping only the ontology service's 404 to not found."""
        try:
            response = self._request("GET", f"{_MODELS}/_/{code}", params={"o": org, "source": "code"})
        except requests.HTTPError as exc:
            if _is_mos_not_found(exc.response):
                raise ConditionNotFound(response=exc.response) from exc
            raise
        return response.json()


def check_subgraph_args(code: object, depth: object, direction: object) -> None:
    """Validate ``condition_subgraph`` arguments; raise ``ValueError`` without echoing the input.

    Shared with the offline fakes so they refuse exactly what the real client refuses.
    """
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        raise ValueError("code must be 'C' or 'B' followed by exactly 7 digits")
    if type(depth) is not int or not _MIN_DEPTH <= depth <= _MAX_DEPTH:
        raise ValueError(f"depth must be an int from {_MIN_DEPTH} to {_MAX_DEPTH}")
    if direction not in _DIRECTIONS:
        raise ValueError("direction must be one of 'out', 'in', 'any'")


def _is_mos_not_found(response: requests.Response | None) -> bool:
    if response is None or response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body == _MOS_NOT_FOUND
