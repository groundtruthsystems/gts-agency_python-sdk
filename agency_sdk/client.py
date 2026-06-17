from typing import TYPE_CHECKING

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.datasets_client import AgencyDatasetsClient
from agency_sdk.delegates.datasource_client import AgencyDatasourceClient
from agency_sdk.delegates.files_client import AgencyFilesClient
from agency_sdk.delegates.ontology_client import AgencyOntologyClient
from agency_sdk.delegates.prompts_client import AgencyPromptsClient
from agency_sdk.delegates.rules_client import AgencyRulesClient

if TYPE_CHECKING:
    from agency_sdk.observability import Observability


class AgencyClient:
    def __init__(
        self,
        token_supplier: CredentialsSupplier,
        base_url: str = "http://localhost:9003",
    ):
        self.base_url = base_url.rstrip("/")
        self.token_supplier = token_supplier
        self.dataset_client = AgencyDatasetsClient(token_supplier=token_supplier, base_url=self.base_url)
        self.datasource_client = AgencyDatasourceClient(token_supplier=token_supplier, base_url=self.base_url)
        self.ontology_client = AgencyOntologyClient(token_supplier=token_supplier, base_url=self.base_url)
        self.prompt_client = AgencyPromptsClient(token_supplier=token_supplier, base_url=self.base_url)
        self.rules_client = AgencyRulesClient(token_supplier=token_supplier, base_url=self.base_url)
        self.files_client = AgencyFilesClient(token_supplier=token_supplier, base_url=self.base_url)
        self._observability: "Observability | None" = None

    def prompts(self) -> AgencyPromptsClient:
        return self.prompt_client

    def dataset(self) -> AgencyDatasetsClient:
        return self.dataset_client

    def datasource(self) -> AgencyDatasourceClient:
        return self.datasource_client

    def ontology(self) -> AgencyOntologyClient:
        return self.ontology_client

    def rules(self) -> AgencyRulesClient:
        return self.rules_client

    def files(self) -> AgencyFilesClient:
        return self.files_client

    def observability(
        self,
        service_name: str,
        service_version: str = "unknown-0",
        *,
        host: str | None = None,
        environment: str = "development",
        org_id: str = "2",
        processor: str = "simple",
        langfuse_public_key: str | None = None,
        langfuse_secret_key: str | None = None,
    ) -> "Observability":
        """Build (once) the OTLP observability bootstrap bound to this client.

        Reuses the client's shared ``CredentialsSupplier`` so a single cached
        token serves both the API client and the telemetry exporters, and
        defaults the OTLP/Langfuse host to this client's ``base_url``. Repeated
        calls return the same instance.

        Requires the optional ``[observability]`` extra; raises
        ``ObservabilityNotInstalled`` (an ``ImportError``) with an install hint
        when it is absent.
        """
        from agency_sdk.observability import Observability, require_observability_deps

        require_observability_deps()
        if self._observability is None:
            self._observability = Observability(
                credentials=self.token_supplier,
                service_name=service_name,
                service_version=service_version,
                host=host or self.base_url,
                environment=environment,
                org_id=org_id,
                processor=processor,
                langfuse_public_key=langfuse_public_key,
                langfuse_secret_key=langfuse_secret_key,
            )
        return self._observability
