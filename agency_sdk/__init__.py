"""GTS Agency Python SDK. Build an ``agency_sdk.client.AgencyClient`` to call the platform.

The names below are re-exported for convenience; importing the package stays light (DTOs only).
"""

from agency_sdk.delegates.graphs_dto import (
    ConditionClassMismatch,
    ConditionNotFound,
    ConditionSubgraph,
    KnowledgeGraphEntry,
    KnowledgeGraphList,
)

__all__ = [
    "ConditionClassMismatch",
    "ConditionNotFound",
    "ConditionSubgraph",
    "KnowledgeGraphEntry",
    "KnowledgeGraphList",
]
