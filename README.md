# Agency Python SDK

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, ontologies, prompts, and rules APIs.

## Installation

```bash
pip install gts-agency-python-sdk
```

For development:

```bash
pip install -e ".[dev]"
```

## Configuration

The examples in this repository use the following environment variables for authentication and configuration:

- `AGENCY_AUTH_URL`: The OAuth2 token endpoint URL.
- `AGENCY_API_URL`: The base URL for the Agency API.
- `AGENCY_ORG_ID`: The organization ID to use for requests.
- `AGENCY_CLIENT_ID`: Your OAuth2 client ID.
- `AGENCY_CLIENT_SECRET`: Your OAuth2 client secret.

## Usage

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

credentials = CredentialsSupplier(
    auth_base_url="https://auth.example.com",
    client_id="your-client-id",
    client_secret="your-client-secret",
)
client = AgencyClient(token_supplier=credentials, base_url="https://api.example.com")
```

### Delegate Clients

Access each API domain through the facade:

- `client.dataset()` — datasets CRUD, filesystem traversal, clone
- `client.datasource()` — datasource and table introspection
- `client.ontology()` — ontology export (JSON, Turtle, ISON) and entity-datasource mappings
- `client.prompts()` — prompt CRUD via command pattern
- `client.rules()` — rule listing, detail, execution, and execution history

### Rules Example

```python
from agency_sdk.delegates.rules_dto import ExecuteRequest

rules = client.rules()

# List rules
result = rules.list(organisation_id=2)
for rule in result.items:
    print(f"{rule.name} (status={rule.active_version_status})")

# Get rule detail
detail = rules.get(rule_id="rule-id", organisation_id=2)

# Execute a rule
response = rules.execute(
    rule_id="rule-id",
    request=ExecuteRequest(organisation=2, context={"key": "value"}, trace=True),
)
print(f"Result: {response.result}")

# List execution history
executions = rules.list_executions(rule_id="rule-id", organisation_id=2)
```

## Examples

```bash
export AGENCY_AUTH_URL="http://localhost:8080/realms/agency/protocol/openid-connect/token"
export AGENCY_API_URL="http://localhost:13001"
export AGENCY_ORG_ID="2"
export AGENCY_CLIENT_ID="your-client-id"
export AGENCY_CLIENT_SECRET="your-client-secret"

python examples/quick_clone_dataset.py
python examples/quick_create_prompt.py
python examples/quick_export_ontology.py
python examples/quick_execute_rule.py
```

