"""Facade tests: AgencyClient composes and exposes the delegate clients."""

from agency_sdk.client import AgencyClient
from agency_sdk.delegates.files_client import AgencyFilesClient
from agency_sdk.delegates.session_client import AgencySessionClient
from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient
from agency_sdk.delegates.work_queue_client import AgencyWorkQueueClient


def test_facade_exposes_files_client(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")

    files = client.files()

    assert isinstance(files, AgencyFilesClient)
    assert files.token_supplier is fake_credentials
    assert files.base_url == "http://cp.test"


def test_facade_returns_same_files_instance(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    assert client.files() is client.files()


def test_facade_exposes_session_vault_client(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")

    vault = client.session_vault()

    assert isinstance(vault, AgencySessionVaultClient)
    assert vault.token_supplier is fake_credentials
    assert vault.base_url == "http://cp.test"


def test_facade_returns_same_session_vault_instance(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    assert client.session_vault() is client.session_vault()


def test_facade_exposes_work_queue_client(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")

    work_queues = client.work_queues()

    assert isinstance(work_queues, AgencyWorkQueueClient)
    assert work_queues.token_supplier is fake_credentials
    assert work_queues.base_url == "http://cp.test"


def test_facade_returns_same_work_queue_instance(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    assert client.work_queues() is client.work_queues()


def test_facade_exposes_session_client(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")

    sessions = client.sessions()

    assert isinstance(sessions, AgencySessionClient)
    assert sessions.token_supplier is fake_credentials
    assert sessions.base_url == "http://cp.test"


def test_facade_returns_same_session_instance(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    assert client.sessions() is client.sessions()
