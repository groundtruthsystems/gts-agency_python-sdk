"""Contract smoke for the graphs delegate against a NON-PRODUCTION comand (opt-in: ``pytest -m integration``).

Scenario: TS-27 list.

Environment:
- ``AGENCY_AUTH_URL``, ``AGENCY_API_URL``, ``AGENCY_CLIENT_ID``, ``AGENCY_CLIENT_SECRET`` for a dedicated
  non-production **service account** (never a personal or pasted browser token); ``AGENCY_ORG_ID`` (default 2).

The probe skips the module when comand is not configured or unreachable. A skip never satisfies the
release gate. Nothing here prints credentials or ``config``.
"""

import os

import pytest
import requests

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
    """TS-27."""
    catalogue = client.graphs().list(_org())

    entry = next((e for e in catalogue.items if e.code == "medical-ontology"), None)
    assert entry is not None, "medical-ontology missing from the catalogue"
    assert entry.enabled, "medical-ontology is not enabled for this org"
