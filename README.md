# Agency Python SDK

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, files, ontologies, prompts, and rules APIs.

## Installation

```bash
pip install gts-agency-python-sdk
```

For development:

```bash
pip install -e ".[dev]"
```

Optional OpenTelemetry tracing support (Langfuse backend):

```bash
pip install gts-agency-python-sdk[observability]
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
- `client.files()` — tenant file storage: list, upload, folder management, delete, signed URLs, `gtsf://` URI resolution, streamed download
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

### Files Example

```python
files = client.files()

# Upload into a folder (multipart; up to 100 MiB per file and per request)
result = files.upload(organisation_id=2, file_paths=["report.pdf"], path="guidelines")
file_id = result.uploaded[0].id

# Resolve a gtsf:// reference (as found in configurations and rule annotations)
signed = files.resolve_gtsf_uri(f"gtsf://{file_id}", organisation_id=2)
print(f"Download until {signed.expires_at}: {signed.signed_url}")

# Or download directly (streamed to disk via the signed URL)
files.download(file_id=file_id, organisation_id=2, target_path="./report.pdf")
```

See [docs/files_storage_flows.md](docs/files_storage_flows.md) for the full
upload/download architecture.

### Observability Example

Opt-in OpenTelemetry tracing + log correlation, shipped to a Langfuse backend and
authenticated with the same credentials as the API client. Requires the
`[observability]` extra.

```python
obs = client.observability("gts-myagent")   # reuses credentials; host defaults to base_url
tracer = obs.init()                          # exporters live; stdlib logging bridged

with obs.agent_run("agent.myagent", correlation_id=cid) as span:
    logger.info("doing work")                # stamped with the span's trace id
    result = do_work()
```

See [docs/observability.md](docs/observability.md) for the full setup, the API,
and migration from a per-agent bootstrap.

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
python examples/quick_files.py
python examples/quick_observability.py   # requires the [observability] extra
```

To verify the SDK end to end against the local platform stack
(`gts-local-environment`), follow [docs/local_e2e.md](docs/local_e2e.md).

