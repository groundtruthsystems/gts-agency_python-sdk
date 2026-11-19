"""Contract smoke for the graphs delegate against a NON-PRODUCTION comand (opt-in: ``pytest -m integration``).

Scenarios: TS-21 (fake/server parity on the paging case table), TS-22 (paged shape, code order), TS-35 (collation).
Required manual check before publishing 0.0.3rc1, against comand with gts-agency cf1d53a3 (#175) deployed.

Environment:
- ``AGENCY_AUTH_URL``, ``AGENCY_API_URL``, ``AGENCY_CLIENT_ID``, ``AGENCY_CLIENT_SECRET`` for a dedicated
  non-production **service account** (never a personal or pasted browser token); ``AGENCY_ORG_ID`` (default 2).

The probe skips the module when comand is not configured or unreachable. A skip never satisfies the
release gate. Nothing here prints credentials or ``config``.
"""

import os

import pytest
import requests

from agency_sdk.domain import Page
from agency_sdk.test.adk_fakes import FakeGraphs

pytestmark = pytest.mark.integration

REQUIRED_ENV = ("AGENCY_AUTH_URL", "AGENCY_API_URL", "AGENCY_CLIENT_ID", "AGENCY_CLIENT_SECRET")


def _org() -> int:
    return int(os.environ.get("AGENCY_ORG_ID", "2"))


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
    """TS-22: the paged shape, code order and the server defaults (page 0, size 10)."""
    catalogue = client.graphs().list(_org())

    assert isinstance(catalogue.page, Page)
    assert (catalogue.page.page, catalogue.page.size) == (0, 10)
    assert catalogue.page.total >= len(catalogue.items)
    codes = [e.code for e in catalogue.items]
    assert codes == sorted(codes), "items are not in code order"
    entry = next((e for e in catalogue.items if e.code == "medical_ontology"), None)
    assert entry is not None, "medical_ontology missing from the catalogue"
    assert entry.enabled, "medical_ontology is not enabled for this org"


def _fake_like(client) -> FakeGraphs:
    """A FakeGraphs seeded with the live catalogue, so both answer every case from the same entries."""
    fake = FakeGraphs()
    total = client.graphs().list(_org()).page.total
    for e in client.graphs().list(_org(), size=max(total, 1)).items:
        fake.add_graph(e.code, name=e.name, description=e.description, enabled=e.enabled)
    return fake


def _outcome(call):
    try:
        result = call()
    except requests.HTTPError as exc:
        error = exc.response.json()["error"]
        return ("error", exc.response.status_code, error["type"], error["message"])
    return ("ok", result.page, [e.code for e in result.items])


@pytest.mark.parametrize(
    "paging",
    [
        {},
        {"page": 0, "size": 1},
        {"page": 1, "size": 1},
        {"page": 99, "size": 5},
        {"size": 0},
        {"page": -1},
        {"size": -1},
    ],
)
def test_fake_matches_live_server(client, paging):
    """TS-21, TS-35: FakeGraphs answers the paging case table exactly as live comand does (incl. code order)."""
    fake = _fake_like(client)

    assert _outcome(lambda: fake.list(_org(), **paging)) == _outcome(lambda: client.graphs().list(_org(), **paging))
