import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.domain import (
    CreatePromptCommand,
    DeletePromptCommand,
    PromptCommand,
    PromptPagedResult,
    PromptResponse,
    PromptsCommand,
    PublishPromptCommand,
    SearchRequest,
    UpdatePromptCommand,
)


class AgencyPromptsClient:

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        self.base_url = base_url.rstrip("/")
        self.token_supplier = token_supplier

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make an HTTP request to the API."""
        url = f"{self.base_url}/api/prompts{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                "Authorization": f"Bearer {self.token_supplier.bearer_token()}",
                "Content-Type": "application/json",
            },
            json=data,
            params=params,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def command(self, command: PromptsCommand) -> dict:
        """Execute a command on the prompts API."""
        return self._make_request("POST", "/_command", data=command.model_dump(mode="json"))

    def create(self, command: CreatePromptCommand) -> dict:
        """Create a new prompt."""
        return self.command(command)

    def search(self, request: SearchRequest) -> PromptPagedResult:
        """Execute a search on the prompts API."""
        return PromptPagedResult(**self._make_request("POST", "/_search", data=request.model_dump(mode="json")))

    def prompt_command(self, prompt_id: str, command: PromptCommand) -> dict:
        """Execute a command on a specific prompt."""
        return self._make_request("POST", f"/{prompt_id}/_command", data=command.model_dump(mode="json"))

    def update(self, prompt_id: str, command: UpdatePromptCommand) -> dict:
        """Update an existing prompt."""
        return self.prompt_command(prompt_id, command)

    def publish(self, prompt_id: str, command: PublishPromptCommand) -> dict:
        """Publish a prompt."""
        return self.prompt_command(prompt_id, command)

    def delete(self, prompt_id: str, command: DeletePromptCommand) -> dict:
        """Delete a prompt."""
        return self.prompt_command(prompt_id, command)

    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> PromptPagedResult:
        """List all prompts for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return PromptPagedResult(**self._make_request("GET", "", params=params))

    def get(self, prompt_id: str, organisation_id: int, version: str = "unpublished") -> PromptResponse:
        """Get a specific prompt by ID."""
        params = {"o": str(organisation_id), "v": version}
        return PromptResponse(**self._make_request("GET", f"/{prompt_id}", params=params))