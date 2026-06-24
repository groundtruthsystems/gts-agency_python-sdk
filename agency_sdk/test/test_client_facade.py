"""Facade tests: AgencyClient composes and exposes the delegate clients."""

from agency_sdk.client import AgencyClient
from agency_sdk.delegates.files_client import AgencyFilesClient
from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient


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
