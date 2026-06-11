"""Offline protocol tests for AgencyFilesClient.

Every test asserts the exact wire contract (URL, query params, headers, body)
verified against gts-agency-control/src/handler/files.rs.
"""

import pytest

from agency_sdk.delegates.files_client import AgencyFilesClient
from agency_sdk.test.test_files_dto import FILE_ENTRY_JSON, FOLDER_ENTRY_JSON

PAGED_JSON = {
    "page": {"page": 0, "size": 50, "total": 2},
    "items": [FILE_ENTRY_JSON, FOLDER_ENTRY_JSON],
}


@pytest.fixture
def client(fake_credentials):
    return AgencyFilesClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestDelete:
    def test_delete_file_hits_id_endpoint_and_returns_none(self, client, stub_requests):
        stub_requests.queue(json_data={"status": "deleted"})

        result = client.delete_file(file_id="abc-123", organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "DELETE"
        assert call.url == "http://cp.test/api/files/abc-123"
        assert call.kwargs["params"] == {"o": "2"}
        assert result is None

    def test_delete_folder_requires_path_param(self, client, stub_requests):
        stub_requests.queue(json_data={"status": "deleted"})

        result = client.delete_folder(organisation_id=2, path="guidelines/2026")

        call = stub_requests.calls[0]
        assert call.method == "DELETE"
        assert call.url == "http://cp.test/api/files/_folder"
        assert call.kwargs["params"] == {"o": "2", "path": "guidelines/2026"}
        assert result is None

    def test_delete_file_propagates_400_for_folder_ids(self, client, stub_requests):
        import requests

        stub_requests.queue(json_data={"message": "Use folder delete for folders"}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.delete_file(file_id="folder-id", organisation_id=2)


SIGNED_URL_JSON = {
    "signed_url": "https://storage.googleapis.com/files/abc?X-Goog-Signature=sig",
    "expires_at": "2026-06-10T12:15:00Z",
    "file": FILE_ENTRY_JSON,
}


class TestSignedUrl:
    def test_signed_url_without_expires(self, client, stub_requests):
        stub_requests.queue(json_data=SIGNED_URL_JSON)

        result = client.signed_url(file_id="abc-123", organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/files/abc-123/_signed-url"
        assert call.kwargs["params"] == {"o": "2"}
        assert result.signed_url == SIGNED_URL_JSON["signed_url"]
        assert result.file.name == "report.pdf"

    def test_signed_url_with_expires(self, client, stub_requests):
        stub_requests.queue(json_data=SIGNED_URL_JSON)

        client.signed_url(file_id="abc-123", organisation_id=2, expires=3600)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "expires": "3600"}

    def test_signed_url_propagates_http_errors(self, client, stub_requests):
        import requests

        stub_requests.queue(json_data={"message": "File not found"}, status_code=404)

        with pytest.raises(requests.HTTPError):
            client.signed_url(file_id="missing", organisation_id=2)


class TestList:
    def test_list_hits_files_endpoint_with_defaults(self, client, stub_requests):
        stub_requests.queue(json_data=PAGED_JSON)

        result = client.list(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/files"
        assert call.kwargs["params"] == {"o": "2", "path": "", "p": "0", "s": "50"}
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert result.page.total == 2
        assert result.items[0].name == "report.pdf"
        assert result.items[1].is_folder is True

    def test_list_passes_path_and_pagination(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 3, "size": 5, "total": 0}, "items": []})

        client.list(organisation_id=9, path="guidelines/2026", page=3, size=5)

        params = stub_requests.calls[0].kwargs["params"]
        assert params == {"o": "9", "path": "guidelines/2026", "p": "3", "s": "5"}
