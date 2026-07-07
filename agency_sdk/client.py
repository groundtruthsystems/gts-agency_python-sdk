import threading
from typing import TYPE_CHECKING

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.datasets_client import AgencyDatasetsClient
from agency_sdk.delegates.datasource_client import AgencyDatasourceClient
from agency_sdk.delegates.files_client import AgencyFilesClient
from agency_sdk.delegates.ontology_client import AgencyOntologyClient
from agency_sdk.delegates.prompts_client import AgencyPromptsClient
from agency_sdk.delegates.rules_client import AgencyRulesClient
from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient

if TYPE_CHECKING:
    from agency_sdk.delegates.gateway_client import AgencyGatewayClient
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
        self.session_vault_client = AgencySessionVaultClient(token_supplier=token_supplier, base_url=self.base_url)
        self._observability: "Observability | None" = None
        self._observability_lock = threading.Lock()
        self._gateway: "AgencyGatewayClient | None" = None
        self._gateway_lock = threading.Lock()

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

    def session_vault(self) -> AgencySessionVaultClient:
        return self.session_vault_client

    def gateway(
        self,
        *,
        org_id: str,
        gateway_base_url: str | None = None,
        environment: str = "production",
    ) -> "AgencyGatewayClient":
        """Build (once) an OpenAI-compatible LLM gateway client bound to this client.

        Targets the org's agentgateway Cloud Run host — never this client's
        control-plane ``base_url`` — reusing the shared ``CredentialsSupplier``
        as the gateway Bearer and stamping the ``x-org`` routing header.
        Repeated calls return the same instance.

        When ``gateway_base_url`` is omitted, the URL is resolved once from
        ``GET /api/agentgateways?o={org_id}`` on the control-plane ``base_url``,
        selecting the ``production`` or ``test`` slot per ``environment``.
        """
        from agency_sdk.delegates.gateway_client import AgencyGatewayClient

        gateway = self._gateway
        if gateway is None:
            # Double-checked locking, mirroring observability(): one build,
            # repeated/concurrent callers share the same instance.
            with self._gateway_lock:
                gateway = self._gateway
                if gateway is None:
                    url = gateway_base_url or self._discover_gateway_url(org_id, environment)
                    gateway = AgencyGatewayClient(
                        token_supplier=self.token_supplier,
                        gateway_base_url=url,
                        org_id=org_id,
                    )
                    self._gateway = gateway
        return gateway

    def _discover_gateway_url(self, org_id: str, environment: str) -> str:
        """Resolve the gateway URL for ``environment`` from the control plane.

        Source-modeled from the control plane's ``AgentGatewayStatusResponse``
        (verification-deferred, docs/gateway_design.md §4.1/§10): reads the
        ``production.url`` / ``test.url`` slot of the org's gateway status.
        """
        from agency_sdk.delegates.gateway_dto import AgentGatewayStatusResponse

        if environment not in ("production", "test"):
            raise ValueError(f"environment must be 'production' or 'test', got {environment!r}")
        response = requests.get(
            f"{self.base_url}/api/agentgateways",
            headers={"Authorization": f"Bearer {self.token_supplier.bearer_token()}"},
            params={"o": org_id},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("items", []) if isinstance(data, dict) else data
        if not items:
            raise ValueError(f"no agent gateway found for org {org_id}; is the gateway enabled?")
        status = AgentGatewayStatusResponse(**items[0])
        slot = status.production if environment == "production" else status.test
        if slot is None or not slot.url:
            raise ValueError(
                f"agent gateway {environment} URL not available for org {org_id} "
                f"(enabled={status.enabled}); pass gateway_base_url explicitly"
            )
        return slot.url

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
        from agency_sdk.observability import Observability, TelemetryConfig, require_observability_deps

        require_observability_deps()
        observability = self._observability
        if observability is None:
            # Double-checked locking: serialize concurrent first-time builds so a
            # single Observability is constructed and cached (repeated calls return
            # the same instance, even under concurrency).
            with self._observability_lock:
                observability = self._observability
                if observability is None:
                    observability = Observability(
                        credentials=self.token_supplier,
                        service_name=service_name,
                        service_version=service_version,
                        config=TelemetryConfig(
                            host=host or self.base_url,
                            environment=environment,
                            org_id=org_id,
                            processor=processor,
                            langfuse_public_key=langfuse_public_key,
                            langfuse_secret_key=langfuse_secret_key,
                        ),
                    )
                    self._observability = observability
        return observability
