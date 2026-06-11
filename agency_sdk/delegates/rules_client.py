from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.rules_dto import (
    ExecuteRequest,
    ExecutionResult,
    ExecutionsPagedResult,
    RuleDetail,
    RulesPagedResult,
)


class AgencyRulesClient:
    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        self.base_url = base_url.rstrip("/")
        self.token_supplier = token_supplier

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/rules{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                "Authorization": f"Bearer {self.token_supplier.bearer_token()}",
                "Content-Type": "application/json",
            },
            json=data,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json() if response.content else {}
        return result

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
