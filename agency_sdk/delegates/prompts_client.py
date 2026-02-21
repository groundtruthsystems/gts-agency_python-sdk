import requests

from typing import Optional, Dict, List

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.domain import PromptsCommand, PromptCommand, CreatePromptCommand, UpdatePromptCommand, \
    PublishPromptCommand, SearchRequest, PromptPagedResult, PromptResponse, DeletePromptCommand


class AgencyPromptsClient:

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = 'http://localhost:9003'):
        self.base_url = base_url.rstrip('/')
        self.token_supplier = token_supplier

    def _make_request(
            self,
            method: str,
            endpoint: str,
            data: Optional[Dict] = None,
            params: Optional[Dict] = None
    ) -> Dict:
        """Make an HTTP request to the API.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            endpoint: API endpoint path
            data: Request body data
            params: Query parameters

        Returns:
            Response data as dictionary
        """
        url = f"{self.base_url}/api/prompts{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                'Authorization': f'Bearer {self.token_supplier.bearer_token()}',
                'Content-Type': 'application/json'
            },
            json=data,
            params=params
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def command(
            self,
            command: PromptsCommand,
    ) -> Dict:
        """Execute a command on the prompts API."""
        return self._make_request('POST', '/_command', data=command.model_dump(mode='json'))

    def create(self, command: CreatePromptCommand) -> Dict:
        """Create a new prompt."""
        return self.command(command)

    def search(
            self,
            request: SearchRequest,
    ) -> PromptPagedResult:
        """Execute a search on the prompts API."""
        return PromptPagedResult(**self._make_request('POST', '/_search', data=request.model_dump(mode='json')))


    def prompt_command(
            self,
            prompt_id: str,
            command: PromptCommand,
    ) -> Dict:
        """Execute a command on the prompts API."""
        return self._make_request('POST', f'/{prompt_id}/_command', data=command.model_dump(mode='json'))


    def update(self, prompt_id: str, command: UpdatePromptCommand) -> Dict:
        """Update an existing prompt."""
        return self.prompt_command(prompt_id, command)


    def publish(self, prompt_id: str, command: PublishPromptCommand) -> Dict:
        """Publish a prompt."""
        return self.prompt_command(prompt_id, command)


    def delete(self, prompt_id: str, command: DeletePromptCommand) -> Dict:
        """Delete a prompt."""
        return self.prompt_command(prompt_id, command)


    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> PromptPagedResult:
        """List all prompts for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return PromptPagedResult(**self._make_request('GET', '', params=params))


    def get(self, prompt_id: str, organisation_id: int, version: str = "unpublished") -> PromptResponse:
        """Get a specific prompt by ID."""
        params = {"o": str(organisation_id), "v": version}
        return PromptResponse(**self._make_request('GET', f"/{prompt_id}", params=params))
