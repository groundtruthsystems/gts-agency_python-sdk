from typing import Any

from pydantic import BaseModel, ConfigDict

from agency_sdk.delegates.datasets_dto import Page


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class RuleSummary(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    organization_id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    active_version: str | None = None
    active_version_status: str | None = None
    has_draft: bool | None = None
    created_at: str
    created_by: str | None = None
    updated_at: str


class RulesPagedResult(BaseModel):
    page: Page
    items: list[RuleSummary]


class RuleVersionSummary(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    version: str
    status: str
    created_at: str


class RuleVersion(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    version: str
    status: str
    jdm_content: dict | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    created_at: str
    created_by: str | None = None
    updated_at: str


class RuleDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    organization_id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    version: RuleVersion
    versions: list[RuleVersionSummary]
    created_at: str
    created_by: str | None = None
    updated_at: str


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    organisation: int
    context: dict
    trace: bool = False
    version_id: str | None = None


class ExecutionResult(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    execution_id: str
    result: dict | None = None
    performance: Any | None = None
    duration_ms: int | None = None
    trace: dict | None = None


class ExecutionHistory(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    decision_model_id: str
    decision_model_version_id: str
    input_context: dict | None = None
    output_result: dict | None = None
    duration_ms: int | None = None
    status: str
    error_message: str | None = None
    executed_at: str
    executed_by: str | None = None


class ExecutionsPagedResult(BaseModel):
    page: Page
    items: list[ExecutionHistory]
