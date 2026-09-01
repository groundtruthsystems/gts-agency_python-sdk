"""Offline protocol tests for AgencyOntologyClient.export.

Pins the Control Plane contract for `GET /api/ontologies/{id}/export`
(query params `o`, `format`, `branch`, `version`; text body vs zip bytes)
documented in the ontology export API reference. `export_snapshot` is the
agent-facing path: JSON parsed into `OntologySnapshot`.
"""

import pytest
import requests

from agency_sdk.delegates.ontology_client import AgencyOntologyClient
from agency_sdk.delegates.ontology_dto import Ontology, OntologySnapshot
from agency_sdk.test.test_ontology_dto import ONTOLOGY_JSON, SNAPSHOT_JSON


@pytest.fixture
def client(fake_credentials):
    return AgencyOntologyClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestList:
    def test_list_hits_ontologies_endpoint_paged(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 10, "total": 1}, "items": [ONTOLOGY_JSON]})

        result = client.list(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/ontologies"
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "10"}
        assert [item.name for item in result.items] == ["claims"]
        assert isinstance(result.items[0], Ontology)
        assert result.items[0].id == ONTOLOGY_JSON["id"]
        assert result.page.total == 1

    def test_list_forwards_pagination(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 1, "size": 5, "total": 0}, "items": []})

        client.list(organisation_id=9, page=1, size=5)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "9", "p": "1", "s": "5"}

    def test_list_forwards_kind_filter(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 10, "total": 0}, "items": []})

        client.list(organisation_id=2, kind="upper")

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "p": "0", "s": "10", "kind": "upper"}


class TestExport:
    def test_default_export_sends_org_and_returns_text(self, client, stub_requests):
        stub_requests.queue(text='{"entities":{},"relations":{}}')

        body = client.export(ontology_id="ont-1", organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/ontologies/ont-1/export"
        assert call.kwargs["params"] == {"o": "2"}
        assert "Content-Type" not in call.kwargs["headers"]
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert body == '{"entities":{},"relations":{}}'

    @pytest.mark.parametrize(
        "export_format",
        ["json", "owl", "turtle", "shacl", "shacl-package", "package"],
    )
    def test_text_formats_are_passed_as_query_param(self, client, stub_requests, export_format):
        stub_requests.queue(text="export-body")

        body = client.export(ontology_id="ont-1", organisation_id=2, export_format=export_format)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "format": export_format}
        assert body == "export-body"

    def test_branch_and_version_are_forwarded(self, client, stub_requests):
        stub_requests.queue(text="v")

        client.export(
            ontology_id="ont-1",
            organisation_id=2,
            export_format="turtle",
            branch="main",
            version="1.0.0",
        )

        assert stub_requests.calls[0].kwargs["params"] == {
            "o": "2",
            "format": "turtle",
            "branch": "main",
            "version": "1.0.0",
        }

    def test_http_errors_propagate(self, client, stub_requests):
        stub_requests.queue(json_data={"error": "Unsupported format: ison"}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.export(ontology_id="ont-1", organisation_id=2, export_format="ison")

    def test_zip_format_raises_rather_than_decoding_as_text(self, client, stub_requests):
        with pytest.raises(ValueError, match="binary"):
            client.export(ontology_id="ont-1", organisation_id=2, export_format="package-zip")

        assert stub_requests.calls == []


class TestExportBytes:
    def test_zip_returns_raw_bytes(self, client, stub_requests):
        stub_requests.queue(content_bytes=b"PK\x03\x04zip-bytes")

        body = client.export_bytes(ontology_id="ont-1", organisation_id=2, export_format="package-zip")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/ontologies/ont-1/export"
        assert call.kwargs["params"] == {"o": "2", "format": "package-zip"}
        assert body == b"PK\x03\x04zip-bytes"

    def test_text_format_still_returns_content_bytes(self, client, stub_requests):
        stub_requests.queue(content_bytes=b"<http://ex> a owl:Class .")

        body = client.export_bytes(ontology_id="ont-1", organisation_id=2, export_format="turtle")

        assert body == b"<http://ex> a owl:Class ."


class TestExportSnapshot:
    def test_parses_json_export_into_typed_snapshot(self, client, stub_requests):
        stub_requests.queue(json_data=SNAPSHOT_JSON)

        snapshot = client.export_snapshot(ontology_id="ont-1", organisation_id=2)

        call = stub_requests.calls[0]
        assert call.url == "http://cp.test/api/ontologies/ont-1/export"
        assert call.kwargs["params"] == {"o": "2", "format": "json"}
        assert isinstance(snapshot, OntologySnapshot)
        assert snapshot.entities["entity_1768310108685_hkrzizdts"].label == "Test"
        assert len(snapshot.relations) == 1

    def test_forwards_branch_and_version(self, client, stub_requests):
        stub_requests.queue(json_data={"entities": {}, "relations": {}})

        client.export_snapshot(ontology_id="ont-1", organisation_id=2, branch="dev", version="2.0.0")

        assert stub_requests.calls[0].kwargs["params"] == {
            "o": "2",
            "format": "json",
            "branch": "dev",
            "version": "2.0.0",
        }
