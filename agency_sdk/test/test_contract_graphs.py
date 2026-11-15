"""Contract smoke for the graphs delegate against a NON-PRODUCTION comand (opt-in: ``pytest -m integration``).

Scenarios: TS-25 known C… condition, TS-26 unknown code, TS-27 list, TS-28 the ontology service's
not-found body, TS-31 authored B… condition.

Environment:
- ``AGENCY_AUTH_URL``, ``AGENCY_API_URL``, ``AGENCY_CLIENT_ID``, ``AGENCY_CLIENT_SECRET`` for a dedicated
  non-production **service account** (never a personal or pasted browser token); ``AGENCY_ORG_ID`` (default 2).
- ``GRAPHS_SMOKE_C_CODE``: an operator-supplied ``C…`` condition that exists in the knowledge graph.
- ``GRAPHS_SMOKE_B_CODE``: an operator-supplied authored ``B…`` condition that exists in the knowledge graph.

The probe skips the module when comand is not configured or unreachable. A missing smoke code **fails**
its scenario instead of skipping. A skip never satisfies the release gate. Nothing here prints credentials,
``config`` or subgraph documents.
"""

import os

import pytest
import requests

from agency_sdk.delegates.graphs_dto import ConditionNotFound

pytestmark = pytest.mark.integration

REQUIRED_ENV = ("AGENCY_AUTH_URL", "AGENCY_API_URL", "AGENCY_CLIENT_ID", "AGENCY_CLIENT_SECRET")
UNKNOWN_CODE = "C9999999"


def _org() -> int:
    return int(os.environ.get("AGENCY_ORG_ID", "2"))


def _smoke_code(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set; the operator must supply it (a missing code fails, never skips)")
    return value


@pytest.fixture(scope="module")
def client():
    """Probe: configured env, a token, and comand answering the knowledge-graph list."""
    missing = [n for n in REQUIRED_ENV if not os.environ.get(n)]
    if missing:
        pytest.skip(f"non-prod comand not configured: {', '.join(missing)}")
    from agency_sdk.client import AgencyClient, CredentialsSupplier

    credentials = CredentialsSupplier(
        auth_base_url=os.environ["AGENCY_AUTH_URL"],
        client_id=os.environ["AGENCY_CLIENT_ID"],
        client_secret=os.environ["AGENCY_CLIENT_SECRET"],
    )
    agency = AgencyClient(token_supplier=credentials, base_url=os.environ["AGENCY_API_URL"])
    try:
        agency.graphs().list(_org())
    except requests.RequestException as exc:
        pytest.skip(f"graphs contract probe failed: {type(exc).__name__}")
    return agency


def test_list_contract(client):
    """TS-27."""
    catalogue = client.graphs().list(_org())

    entry = next((e for e in catalogue.items if e.code == "medical-ontology"), None)
    assert entry is not None, "medical-ontology missing from the catalogue"
    assert entry.enabled, "medical-ontology is not enabled for this org"


def test_known_condition(client):
    """TS-25: proves the proxy routing end to end."""
    subgraph = client.graphs().condition_subgraph(_org(), _smoke_code("GRAPHS_SMOKE_C_CODE"))

    assert subgraph.root["class"] == "condition"
    assert isinstance(subgraph.vertices, list) and isinstance(subgraph.edges, list)


def test_authored_b_code(client):
    """TS-31: a MOS 404 here means authored B ids are not reachable via source=code; amend the design."""
    try:
        subgraph = client.graphs().condition_subgraph(_org(), _smoke_code("GRAPHS_SMOKE_B_CODE"))
    except ConditionNotFound:
        pytest.fail("authored B… id not found via source=code (design OQ3 is false): amend the design before release")
    assert subgraph.root["class"] == "condition"


def test_unknown_condition(client):
    """TS-26."""
    with pytest.raises(ConditionNotFound):
        client.graphs().condition_subgraph(_org(), UNKNOWN_CODE)


def test_mos_not_found_body_contract(client):
    """TS-28: pins the exact body the client's not-found mapping depends on."""
    graphs = client.graphs()
    url = f"{graphs.base_url}{graphs.api_path}/medical-ontology/api/models/_/{UNKNOWN_CODE}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {graphs.token_supplier.bearer_token()}"},
        params={"o": str(_org()), "source": "code"},
        timeout=30,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
