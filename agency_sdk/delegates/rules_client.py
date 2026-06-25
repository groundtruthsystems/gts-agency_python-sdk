from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.rules_dto import (
    ExecuteRequest,
    ExecutionResult,
    ExecutionsPagedResult,
    RuleDetail,
    RulesPagedResult,
)


class AgencyRulesClient(BaseDelegateClient):
    api_path = "/api/rules"

    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> RulesPagedResult:
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return RulesPagedResult(**self._make_request("GET", "", params=params))

    def get(self, rule_id: str, organisation_id: int, version_id: str | None = None) -> RuleDetail:
        params: dict[str, str] = {"o": str(organisation_id)}
        if version_id is not None:
            params["v"] = version_id
        return RuleDetail(**self._make_request("GET", f"/{rule_id}", params=params))

    def execute(self, rule_id: str, request: ExecuteRequest) -> ExecutionResult:
        data = request.model_dump(mode="json", by_alias=True)
        result = self._make_request("POST", f"/{rule_id}/_execute", data=data)
        return ExecutionResult(**result)

    def list_executions(
        self, rule_id: str, organisation_id: int, page: int = 0, size: int = 10
    ) -> ExecutionsPagedResult:
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return ExecutionsPagedResult(**self._make_request("GET", f"/{rule_id}/executions", params=params))
