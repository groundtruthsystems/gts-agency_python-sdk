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
