from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.datasets_client import AgencyDatasetsClient
from agency_sdk.delegates.datasource_client import AgencyDatasourceClient
from agency_sdk.delegates.ontology_client import AgencyOntologyClient
from agency_sdk.delegates.prompts_client import AgencyPromptsClient


class AgencyClient:

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = 'http://localhost:8080/realms/agency/protocol/openid-connect/token'):
        self.base_url = base_url.rstrip('/')
        self.token_supplier = token_supplier
        self.dataset_client = AgencyDatasetsClient(token_supplier=token_supplier, base_url=self.base_url)
        self.datasource_client = AgencyDatasourceClient(token_supplier=token_supplier, base_url=self.base_url)
        self.ontology_client = AgencyOntologyClient(token_supplier=token_supplier, base_url=self.base_url)
        self.prompt_client = AgencyPromptsClient(token_supplier=token_supplier, base_url=self.base_url)

    def prompts(self):
        return self.prompt_client

    def dataset(self):
        return self.dataset_client

    def datasource(self) -> AgencyDatasourceClient:
        return self.datasource_client

    def ontology(self) -> AgencyOntologyClient:
        return self.ontology_client