from typing import Any

from agency_sdk.delegates.base_client import BaseDelegateClient
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


class AgencyPromptsClient(BaseDelegateClient):
    api_path = "/api/prompts"

    def command(self, command: PromptsCommand) -> dict[str, Any]:
        """Execute a command on the prompts API."""
        return self._make_request("POST", "/_command", data=command.model_dump(mode="json"))

    def create(self, command: CreatePromptCommand) -> dict[str, Any]:
        """Create a new prompt."""
        return self.command(command)

    def search(self, request: SearchRequest) -> PromptPagedResult:
        """Execute a search on the prompts API."""
        return PromptPagedResult(**self._make_request("POST", "/_search", data=request.model_dump(mode="json")))

    def prompt_command(self, prompt_id: str, command: PromptCommand) -> dict[str, Any]:
        """Execute a command on a specific prompt."""
        return self._make_request("POST", f"/{prompt_id}/_command", data=command.model_dump(mode="json"))

    def update(self, prompt_id: str, command: UpdatePromptCommand) -> dict[str, Any]:
        """Update an existing prompt."""
        return self.prompt_command(prompt_id, command)

    def publish(self, prompt_id: str, command: PublishPromptCommand) -> dict[str, Any]:
        """Publish a prompt."""
        return self.prompt_command(prompt_id, command)

    def delete(self, prompt_id: str, command: DeletePromptCommand) -> dict[str, Any]:
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
