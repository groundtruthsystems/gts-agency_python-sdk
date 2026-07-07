"""Facade tests for AgencyClient.gateway(...) and control-plane URL discovery.

The accessor mirrors the observability precedent (DCL cache, shared
CredentialsSupplier) but targets the gateway's own Cloud Run host. Discovery
(GET /api/agentgateways?o={org}) is source-modeled from the control-plane DTO
and verification-deferred (design §4/§10 decision 1) — these offline tests are
its only verification for now.
"""

import threading
import time

import pytest

import agency_sdk.delegates.gateway_client as gateway_module
from agency_sdk.client import AgencyClient
from agency_sdk.delegates.gateway_client import AgencyGatewayClient

PROD_URL = "https://agentgateway-org-2-wadexavawa-uk.a.run.app"
TEST_URL = "https://agentgateway-org-2-test-wadexavawa-uk.a.run.app"

DISCOVERY_ITEM = {
    "enabled": True,
    "code": "gateway-1a2b3c4d",
    "production": {"environment": "production", "status": "ready", "url": PROD_URL, "version": 3},
    "test": {"environment": "test", "status": "ready", "url": TEST_URL, "version": 4},
}


@pytest.fixture
def client(fake_credentials):
    return AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")


def test_gateway_with_explicit_url_builds_bound_client(client, fake_credentials):
    gateway = client.gateway(org_id="2", gateway_base_url="http://gw.test:4000/")

    assert isinstance(gateway, AgencyGatewayClient)
    assert gateway.token_supplier is fake_credentials  # shared creds, one cached token
    assert gateway.gateway_base_url == "http://gw.test:4000"
    assert gateway.org_id == "2"


def test_gateway_returns_same_instance(client):
    first = client.gateway(org_id="2", gateway_base_url="http://gw.test:4000")

    assert client.gateway(org_id="2", gateway_base_url="http://gw.test:4000") is first


def test_gateway_is_thread_safe(monkeypatch, client):
    real_cls = gateway_module.AgencyGatewayClient
    count = {"n": 0}
    count_lock = threading.Lock()

    def counting(*args, **kwargs):
        with count_lock:
            count["n"] += 1
        time.sleep(0.01)  # widen the race window so an unguarded build builds N times
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(gateway_module, "AgencyGatewayClient", counting)

    n_threads = 8
    results: list = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()  # release all threads together
        instance = client.gateway(org_id="2", gateway_base_url="http://gw.test:4000")
        with results_lock:
            results.append(instance)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert count["n"] == 1  # constructed exactly once despite concurrent callers
    assert len(results) == n_threads
    assert all(instance is results[0] for instance in results)


def test_gateway_discovers_production_url_when_base_url_omitted(stub_requests, client):
    stub_requests.queue(json_data=[DISCOVERY_ITEM])

    gateway = client.gateway(org_id="2")

    assert gateway.gateway_base_url == PROD_URL
    call = stub_requests.calls[0]
    assert call.method == "GET"
    assert call.url == "http://cp.test/api/agentgateways"  # control-plane host, not gateway
    assert call.kwargs["params"] == {"o": "2"}
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"


def test_gateway_discovers_test_url_for_environment_test(stub_requests, client):
    stub_requests.queue(json_data=[DISCOVERY_ITEM])

    gateway = client.gateway(org_id="2", environment="test")

    assert gateway.gateway_base_url == TEST_URL


def test_discovery_accepts_page_wrapped_items(stub_requests, client):
    # Control-plane list endpoints commonly wrap results; tolerate {"items": [...]}.
    stub_requests.queue(json_data={"items": [DISCOVERY_ITEM]})

    gateway = client.gateway(org_id="2")

    assert gateway.gateway_base_url == PROD_URL


def test_discovery_raises_on_empty_result(stub_requests, client):
    stub_requests.queue(json_data=[])

    with pytest.raises(ValueError, match="org 2"):
        client.gateway(org_id="2")


def test_discovery_raises_when_slot_or_url_missing(stub_requests, client):
    # Gateway enabled but the test slot is still provisioning (no URL yet).
    item = {
        "enabled": True,
        "production": {"environment": "production", "status": "ready", "url": PROD_URL},
        "test": {"environment": "test", "status": "provisioning", "url": None},
    }
    stub_requests.queue(json_data=[item])

    with pytest.raises(ValueError, match="test"):
        client.gateway(org_id="2", environment="test")


def test_discovery_rejects_unknown_environment(client):
    with pytest.raises(ValueError, match="environment"):
        client.gateway(org_id="2", environment="staging")
