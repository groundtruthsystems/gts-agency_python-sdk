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


def signed_url_for(content: bytes) -> dict:
    """A signed-url response whose file size_bytes matches the given content."""
    return {
        "signed_url": "https://storage.googleapis.com/files/abc?X-Goog-Signature=sig",
        "expires_at": "2026-06-10T12:15:00Z",
        "file": {**FILE_ENTRY_JSON, "size_bytes": len(content)},
    }


class TestDownload:
    def test_download_streams_signed_url_to_target(self, client, stub_requests, tmp_path):
        content = b"%PDF-1.7 fake content"
        stub_requests.queue(json_data=signed_url_for(content))
        stub_requests.queue(content_bytes=content)
        target = tmp_path / "nested" / "dir" / "out.pdf"

        entry = client.download(file_id="abc-123", organisation_id=2, target_path=target)

        signed_call, blob_call = stub_requests.calls
        assert signed_call.url == "http://cp.test/api/files/abc-123/_signed-url"
        assert blob_call.method == "GET"
        assert blob_call.kwargs["stream"] is True
        assert blob_call.kwargs["timeout"] == 300
        assert target.read_bytes() == content
        assert entry.name == "report.pdf"

    def test_download_accepts_string_target_path(self, client, stub_requests, tmp_path):
        content = b"x"
        stub_requests.queue(json_data=signed_url_for(content))
        stub_requests.queue(content_bytes=content)

        client.download(file_id="abc-123", organisation_id=2, target_path=str(tmp_path / "plain.bin"))

        assert (tmp_path / "plain.bin").read_bytes() == content

    def test_download_raises_on_truncated_stream(self, client, stub_requests, tmp_path):
        # signed URL advertises a larger size than the bytes actually streamed
        stub_requests.queue(json_data=signed_url_for(b"x" * 100))
        stub_requests.queue(content_bytes=b"x" * 40)

        with pytest.raises(IOError):
            client.download(file_id="abc-123", organisation_id=2, target_path=tmp_path / "short.bin")


class TestResolveGtsfUri:
    def test_resolves_valid_uri_via_signed_url_endpoint(self, client, stub_requests):
        stub_requests.queue(json_data=SIGNED_URL_JSON)

        result = client.resolve_gtsf_uri("gtsf://550e8400-e29b-41d4-a716-446655440000", organisation_id=2)

        call = stub_requests.calls[0]
        assert call.url == "http://cp.test/api/files/550e8400-e29b-41d4-a716-446655440000/_signed-url"
        assert call.kwargs["params"] == {"o": "2"}
        assert result.signed_url == SIGNED_URL_JSON["signed_url"]

    def test_resolve_forwards_expires(self, client, stub_requests):
        stub_requests.queue(json_data=SIGNED_URL_JSON)

        client.resolve_gtsf_uri("gtsf://abc-123", organisation_id=2, expires=120)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "expires": "120"}

    @pytest.mark.parametrize(
        "bad_uri",
        [
            "https://example.com/file",  # wrong scheme
            "gtsf://",  # empty id
            "gtsf://abc/123",  # embedded slash
            "GTSF://abc-123",  # uppercase scheme (strict lowercase)
            "abc-123",  # bare id, no scheme
            "",  # empty string
        ],
    )
    def test_rejects_malformed_uris_before_any_network_call(self, client, stub_requests, bad_uri):
        with pytest.raises(ValueError):
            client.resolve_gtsf_uri(bad_uri, organisation_id=2)

        assert stub_requests.calls == []


class TestUpload:
    def test_upload_sends_repeated_file_multipart_fields(self, client, stub_requests, tmp_path):
        report = tmp_path / "report.txt"
        report.write_bytes(b"hello")
        blob = tmp_path / "data.unknownext"
        blob.write_bytes(b"\x00\x01")
        stub_requests.queue(json_data={"uploaded": [FILE_ENTRY_JSON, FOLDER_ENTRY_JSON]})

        result = client.upload(organisation_id=2, file_paths=[report, str(blob)], path="guidelines")

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/files/_upload"
        assert call.kwargs["params"] == {"o": "2", "path": "guidelines"}
        assert call.kwargs["timeout"] == 300

        files = call.kwargs["files"]
        assert [field for field, _ in files] == ["file", "file"]
        assert files[0][1][0] == "report.txt"
        assert files[0][1][2] == "text/plain"
        assert files[1][1][0] == "data.unknownext"
        assert files[1][1][2] is None
        assert len(result.uploaded) == 2

    def test_upload_has_no_json_content_type_header(self, client, stub_requests, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"x")
        stub_requests.queue(json_data={"uploaded": [FILE_ENTRY_JSON]})

        client.upload(organisation_id=2, file_paths=[f])

        headers = stub_requests.calls[0].kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-token"
        assert "Content-Type" not in headers
        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "path": ""}

    def test_upload_rejects_empty_file_list_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.upload(organisation_id=2, file_paths=[])

        assert stub_requests.calls == []


class TestCreateFolder:
    def test_create_folder_posts_json_body(self, client, stub_requests):
        stub_requests.queue(json_data=FOLDER_ENTRY_JSON)

        result = client.create_folder(organisation_id=2, name="guidelines")

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/files/_folder"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {"folder_path": "", "name": "guidelines"}
        assert result.is_folder is True
        assert result.name == "guidelines"

    def test_create_folder_under_parent_path(self, client, stub_requests):
        stub_requests.queue(json_data=FOLDER_ENTRY_JSON)

        client.create_folder(organisation_id=2, name="2026", folder_path="guidelines")

        assert stub_requests.calls[0].kwargs["json"] == {"folder_path": "guidelines", "name": "2026"}

    def test_create_folder_propagates_409_conflict(self, client, stub_requests):
        import requests

        stub_requests.queue(json_data={"message": "already exists"}, status_code=409)

        with pytest.raises(requests.HTTPError):
            client.create_folder(organisation_id=2, name="guidelines")


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
