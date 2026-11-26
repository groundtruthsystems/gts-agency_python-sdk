"""Offline protocol tests for AgencyFilesClient.

Every test asserts the exact wire contract (URL, query params, headers, body)
verified against gts-agency-control/src/handler/files.rs.
"""

from pathlib import Path

import pytest
import requests

from agency_sdk.delegates.files_client import AgencyFilesClient, FilesUploadError
from agency_sdk.test.test_files_dto import FILE_ENTRY_JSON, FOLDER_ENTRY_JSON

PAGED_JSON = {
    "page": {"page": 0, "size": 50, "total": 2},
    "items": [FILE_ENTRY_JSON, FOLDER_ENTRY_JSON],
}

SIGNED_URL_JSON = {
    "signed_url": "https://storage.googleapis.com/files/abc?X-Goog-Signature=sig",
    "expires_at": "2026-06-10T12:15:00Z",
    "file": FILE_ENTRY_JSON,
}


@pytest.fixture
def client(fake_credentials):
    return AgencyFilesClient(token_supplier=fake_credentials, base_url="http://cp.test/", retry_base_delay=0)


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


MIB = 1024 * 1024


def limits_json(threshold: int = MIB, maximum: int = 5 * 1024 * MIB, part_size: int = 64 * MIB) -> dict:
    return {"proxied_upload_max_bytes": threshold, "max_upload_bytes": maximum, "part_size": part_size}


def entry_json(name: str, size: int) -> dict:
    return {**FILE_ENTRY_JSON, "id": f"id-{name}", "name": name, "path": name, "size_bytes": size}


def part_url(number: int, tag: str = "") -> str:
    return f"https://bucket.test/obj?partNumber={number}&X-Amz-Signature=SECRET{tag}{number}"


SESSION_URI = "https://storage.googleapis.com/upload/obj?upload_id=SECRET-GCS"


def s3_session(size: int, part_size: int, file_id: str = "fid-1") -> dict:
    count = -(-size // part_size)
    return {
        "file_id": file_id,
        "strategy": "s3_multipart",
        "part_size": part_size,
        "expires_at": "2026-11-25T12:00:00Z",
        "limits": limits_json(),
        "parts": [{"part_number": n, "url": part_url(n)} for n in range(1, count + 1)],
        "parts_expire_at": "2026-11-25T11:00:00Z",
    }


def gcs_session(part_size: int, file_id: str = "fid-1") -> dict:
    return {
        "file_id": file_id,
        "strategy": "gcs_resumable",
        "part_size": part_size,
        "expires_at": "2026-11-25T12:00:00Z",
        "limits": limits_json(),
        "session_uri": SESSION_URI,
    }


def error_json(code: str | None = None, error_type: str = "INVALID_PARAMS") -> dict:
    error: dict = {"type": error_type, "req_uuid": "r-1", "message": "refused"}
    if code is not None:
        error["details"] = {"code": code}
    return {"error": error}


def write_file(tmp_path, name: str, size: int):
    path = tmp_path / name
    path.write_bytes(bytes(i % 251 for i in range(size)))
    return path


def calls_to(recorder, suffix: str, method: str | None = None) -> list:
    return [c for c in recorder.calls if c.url.endswith(suffix) and (method is None or c.method == method)]


def bucket_puts(recorder) -> list:
    return [c for c in recorder.calls if c.method == "PUT"]


class TestLimits:
    def test_limits_reads_limits_endpoint(self, client, stub_requests):
        stub_requests.queue(json_data=limits_json())

        limits = client.limits(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/files/_limits"
        assert call.kwargs["params"] == {"o": "2"}
        assert limits.proxied_upload_max_bytes == MIB


class TestUploadProxied:
    def test_upload_sends_repeated_file_multipart_fields(self, client, stub_requests, tmp_path):
        report = tmp_path / "report.txt"
        report.write_bytes(b"hello")
        blob = tmp_path / "data.unknownext"
        blob.write_bytes(b"\x00\x01")
        stub_requests.queue(json_data=limits_json())
        stub_requests.queue(json_data={"uploaded": [FILE_ENTRY_JSON, FOLDER_ENTRY_JSON]})

        result = client.upload(organisation_id=2, file_paths=[report, str(blob)], path="guidelines")

        call = stub_requests.calls[1]
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
        stub_requests.queue(json_data=limits_json())
        stub_requests.queue(json_data={"uploaded": [FILE_ENTRY_JSON]})

        client.upload(organisation_id=2, file_paths=[f])

        headers = stub_requests.calls[1].kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-token"
        assert "Content-Type" not in headers
        assert stub_requests.calls[1].kwargs["params"] == {"o": "2", "path": ""}

    def test_upload_rejects_empty_file_list_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.upload(organisation_id=2, file_paths=[])

        assert stub_requests.calls == []

    def test_upload_rejects_missing_file_and_directory_before_network(self, client, stub_requests, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            client.upload(organisation_id=2, file_paths=[tmp_path / "nope.txt"])
        with pytest.raises(ValueError, match="regular file"):
            client.upload(organisation_id=2, file_paths=[tmp_path])

        assert stub_requests.calls == []

    def test_file_at_threshold_goes_to_proxied_upload(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "edge.bin", MIB)
        stub_requests.queue(json_data=limits_json(threshold=MIB))
        stub_requests.queue(json_data={"uploaded": [entry_json("edge.bin", MIB)]})

        result = client.upload(organisation_id=2, file_paths=[f])

        assert [c.url.rsplit("/", 1)[-1] for c in stub_requests.calls] == ["_limits", "_upload"]
        assert result.uploaded[0].size_bytes == MIB

    def test_small_files_are_batched_within_threshold_in_order(self, client, stub_requests, tmp_path):
        names = [f"f{i}.bin" for i in range(5)]
        paths = [write_file(tmp_path, n, 300 * 1024) for n in names]
        stub_requests.queue(json_data=limits_json(threshold=MIB))
        stub_requests.queue(json_data={"uploaded": [entry_json(n, 300 * 1024) for n in names[:3]]})
        stub_requests.queue(json_data={"uploaded": [entry_json(n, 300 * 1024) for n in names[3:]]})

        result = client.upload(organisation_id=2, file_paths=paths)

        uploads = calls_to(stub_requests, "/_upload")
        assert [[f[1][0] for f in c.kwargs["files"]] for c in uploads] == [names[:3], names[3:]]
        assert [e.name for e in result.uploaded] == names


class TestUploadRouting:
    def test_file_over_threshold_opens_session(self, client, stub_requests, tmp_path):
        size = MIB + 1
        f = write_file(tmp_path, "big.pdf", size)
        stub_requests.queue(json_data=limits_json(threshold=MIB, part_size=MIB))
        stub_requests.queue(json_data=s3_session(size, MIB), status_code=201)
        stub_requests.queue()  # part 1
        stub_requests.queue()  # part 2
        stub_requests.queue(json_data=entry_json("big.pdf", size))

        result = client.upload(organisation_id=2, file_paths=[f], path="docs")

        initiate = stub_requests.calls[1]
        assert initiate.method == "POST"
        assert initiate.url == "http://cp.test/api/files/_upload-session"
        assert initiate.kwargs["params"] == {"o": "2"}
        assert initiate.kwargs["json"] == {
            "folder_path": "docs",
            "filename": "big.pdf",
            "size": size,
            "content_type": "application/pdf",
        }
        assert calls_to(stub_requests, "/_upload") == []
        assert len(bucket_puts(stub_requests)) == 2
        assert result.uploaded[0].size_bytes == size

    def test_mixed_sizes_keep_file_paths_order(self, client, stub_requests, tmp_path):
        small1 = write_file(tmp_path, "s1.txt", 10)
        big = write_file(tmp_path, "big.bin", 100)
        small2 = write_file(tmp_path, "s2.txt", 10)
        stub_requests.queue(json_data=limits_json(threshold=50, part_size=100))
        stub_requests.queue(json_data={"uploaded": [entry_json("s1.txt", 10)]})
        stub_requests.queue(json_data=s3_session(100, 100), status_code=201)
        stub_requests.queue()
        stub_requests.queue(json_data=entry_json("big.bin", 100))
        stub_requests.queue(json_data={"uploaded": [entry_json("s2.txt", 10)]})

        result = client.upload(organisation_id=2, file_paths=[small1, big, small2])

        assert [e.name for e in result.uploaded] == ["s1.txt", "big.bin", "s2.txt"]
        assert len(calls_to(stub_requests, "/_upload")) == 2

    def test_file_over_maximum_is_refused_before_any_write(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "huge.bin", 200)
        stub_requests.queue(json_data=limits_json(threshold=50, maximum=100))

        with pytest.raises(ValueError, match="huge.bin"):
            client.upload(organisation_id=2, file_paths=[f])

        assert [c.method for c in stub_requests.calls] == ["GET"]


class TestS3Session:
    def _start(self, stub_requests, tmp_path, size: int = 100, part_size: int = 40):
        f = write_file(tmp_path, "big.bin", size)
        stub_requests.queue(json_data=limits_json(threshold=50, part_size=part_size))
        stub_requests.queue(json_data=s3_session(size, part_size), status_code=201)
        return f

    def test_parts_carry_correct_byte_ranges_and_no_auth(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        content = f.read_bytes()
        for _ in range(3):
            stub_requests.queue()
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        result = client.upload(organisation_id=2, file_paths=[f])

        puts = bucket_puts(stub_requests)
        assert [c.url for c in puts] == [part_url(1), part_url(2), part_url(3)]
        assert [c.kwargs["data"] for c in puts] == [content[0:40], content[40:80], content[80:100]]
        for c in puts:
            assert "Authorization" not in c.kwargs["headers"]
            assert c.kwargs["timeout"] == (10, 300)
        complete = stub_requests.calls[-1]
        assert complete.method == "POST"
        assert complete.url == "http://cp.test/api/files/_upload-session/fid-1/_complete"
        assert complete.kwargs["params"] == {"o": "2"}
        assert calls_to(stub_requests, "/fid-1", "DELETE") == []
        assert result.uploaded[0].name == "big.bin"

    def test_403_re_signs_the_part_and_retries(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        stub_requests.queue()  # part 1
        stub_requests.queue(status_code=403)  # part 2: signature expired
        stub_requests.queue(
            json_data={"parts": [{"part_number": 2, "url": part_url(2, "NEW")}], "expires_at": "2026-11-25T13:00:00Z"}
        )
        stub_requests.queue()  # part 2 retry
        stub_requests.queue()  # part 3
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        client.upload(organisation_id=2, file_paths=[f])

        resign = calls_to(stub_requests, "/fid-1/_parts")
        assert len(resign) == 1
        assert resign[0].kwargs["json"] == {"part_numbers": [2]}
        assert resign[0].kwargs["params"] == {"o": "2"}
        assert [c.url for c in bucket_puts(stub_requests)] == [
            part_url(1),
            part_url(2),
            part_url(2, "NEW"),
            part_url(3),
        ]

    def test_exhausted_retries_raise_without_url_and_cancel(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        for _ in range(client.max_retries + 1):
            stub_requests.queue(status_code=503)
        stub_requests.queue(json_data={"status": "deleted"})  # cancel

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        err = excinfo.value
        assert isinstance(err, requests.HTTPError)
        assert "SECRET" not in str(err) and "SECRET" not in str(err.__cause__)
        assert "part 1" in str(err)
        assert err.uploaded == []
        assert len(bucket_puts(stub_requests)) == client.max_retries + 1
        cancel = stub_requests.calls[-1]
        assert cancel.method == "DELETE"
        assert cancel.url == "http://cp.test/api/files/_upload-session/fid-1"
        assert cancel.kwargs["params"] == {"o": "2"}

    def test_connection_errors_never_leak_the_url(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        for _ in range(client.max_retries + 1):
            stub_requests.queue_exception(requests.ConnectionError(f"Max retries exceeded with url: {part_url(1)}"))
        stub_requests.queue(json_data={"status": "deleted"})

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        assert "SECRET" not in str(excinfo.value)
        assert "SECRET" not in str(excinfo.value.__cause__)
        assert "ConnectionError" in str(excinfo.value)

    def test_cancel_errors_are_ignored(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        for _ in range(client.max_retries + 1):
            stub_requests.queue(status_code=500)
        stub_requests.queue(json_data=error_json("upload_already_complete"), status_code=409)

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        assert "part 1" in str(excinfo.value)  # the original error, not the cancel's

    def test_session_reads_one_part_at_a_time(self, client, stub_requests, tmp_path, monkeypatch):
        f = self._start(stub_requests, tmp_path)
        for _ in range(3):
            stub_requests.queue()
        stub_requests.queue(json_data=entry_json("big.bin", 100))
        reads: list[int] = []
        real_open = Path.open

        class SpyHandle:
            def __init__(self, handle):
                self._handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._handle.close()

            def seek(self, offset, whence=0):
                return self._handle.seek(offset, whence)

            def read(self, size=-1):
                reads.append(size)
                return self._handle.read(size)

        monkeypatch.setattr(Path, "open", lambda self, *a, **kw: SpyHandle(real_open(self, *a, **kw)))

        client.upload(organisation_id=2, file_paths=[f])

        assert reads == [40, 40, 20]


class TestGcsSession:
    def _start(self, stub_requests, tmp_path, size: int = 100, part_size: int = 40):
        f = write_file(tmp_path, "big.bin", size)
        stub_requests.queue(json_data=limits_json(threshold=50, part_size=part_size))
        stub_requests.queue(json_data=gcs_session(part_size), status_code=201)
        return f

    def test_chunks_carry_content_range_and_follow_308(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        content = f.read_bytes()
        stub_requests.queue(status_code=308, headers={"Range": "bytes=0-39"})
        stub_requests.queue(status_code=308, headers={"Range": "bytes=0-79"})
        stub_requests.queue(status_code=200)
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        client.upload(organisation_id=2, file_paths=[f])

        puts = bucket_puts(stub_requests)
        assert [c.kwargs["headers"]["Content-Range"] for c in puts] == [
            "bytes 0-39/100",
            "bytes 40-79/100",
            "bytes 80-99/100",
        ]
        assert [c.kwargs["data"] for c in puts] == [content[0:40], content[40:80], content[80:100]]
        assert all(c.url == SESSION_URI and "Authorization" not in c.kwargs["headers"] for c in puts)
        assert stub_requests.calls[-1].url.endswith("/_upload-session/fid-1/_complete")

    def test_connection_error_resumes_from_status_query(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        content = f.read_bytes()
        stub_requests.queue(status_code=308, headers={"Range": "bytes=0-39"})
        stub_requests.queue_exception(requests.ConnectionError(SESSION_URI))  # chunk 2
        stub_requests.queue(status_code=308, headers={"Range": "bytes=0-59"})  # status query
        stub_requests.queue(status_code=201)  # resumed from byte 60
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        client.upload(organisation_id=2, file_paths=[f])

        puts = bucket_puts(stub_requests)
        status_query = puts[2]
        assert status_query.kwargs["headers"] == {"Content-Range": "bytes */100", "Content-Length": "0"}
        assert status_query.kwargs["data"] == b""
        assert puts[3].kwargs["headers"]["Content-Range"] == "bytes 60-99/100"
        assert puts[3].kwargs["data"] == content[60:100]

    def test_308_without_range_restarts_from_zero(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path, size=60, part_size=60)
        stub_requests.queue(status_code=308)  # nothing persisted
        stub_requests.queue(status_code=200)
        stub_requests.queue(json_data=entry_json("big.bin", 60))

        client.upload(organisation_id=2, file_paths=[f])

        assert [c.kwargs["headers"]["Content-Range"] for c in bucket_puts(stub_requests)] == [
            "bytes 0-59/60",
            "bytes 0-59/60",
        ]

    def test_no_progress_fails_after_max_retries_and_cancels(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path)
        stub_requests.queue(status_code=500)
        for _ in range(client.max_retries):
            stub_requests.queue(status_code=308)  # status query: nothing received (not counted)
            stub_requests.queue(status_code=500)
        stub_requests.queue(json_data={"status": "deleted"})

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        assert "byte offset 0" in str(excinfo.value)
        assert "SECRET" not in str(excinfo.value)
        sends = [c for c in bucket_puts(stub_requests) if c.kwargs["headers"]["Content-Range"] == "bytes 0-39/100"]
        assert len(sends) == client.max_retries + 1
        assert stub_requests.calls[-1].method == "DELETE"
        assert stub_requests._responses == []

    def test_all_bytes_sent_but_not_finalised_does_not_loop(self, client, stub_requests, tmp_path):
        f = self._start(stub_requests, tmp_path, size=60, part_size=60)
        # the chunk lands (progress), then every status query reports the same range without finalising
        for _ in range(client.max_retries + 2):
            stub_requests.queue(status_code=308, headers={"Range": "bytes=0-59"})
        stub_requests.queue(json_data={"status": "deleted"})

        with pytest.raises(FilesUploadError, match="no progress"):
            client.upload(organisation_id=2, file_paths=[f])

        assert stub_requests.calls[-1].method == "DELETE"

    def test_malformed_session_response_does_not_leak_the_uri(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50))
        broken = {k: v for k, v in gcs_session(40).items() if k != "file_id"}
        stub_requests.queue(json_data=broken, status_code=201)

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        assert "SECRET" not in str(excinfo.value)
        assert "SECRET" not in str(excinfo.value.__cause__)


class TestComplete:
    def _start_s3(self, stub_requests, tmp_path):
        f = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50, part_size=100))
        stub_requests.queue(json_data=s3_session(100, 100), status_code=201)
        stub_requests.queue()  # the single part
        return f

    def test_upload_incomplete_is_retried(self, client, stub_requests, tmp_path):
        f = self._start_s3(stub_requests, tmp_path)
        stub_requests.queue(json_data=error_json("upload_incomplete"), status_code=409)
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        result = client.upload(organisation_id=2, file_paths=[f])

        assert len(calls_to(stub_requests, "/_complete")) == 2
        assert result.uploaded[0].name == "big.bin"

    def test_conflict_retry_is_retried(self, client, stub_requests, tmp_path):
        f = self._start_s3(stub_requests, tmp_path)
        stub_requests.queue(json_data={"error": {"type": "CONFLICT_RETRY", "message": "busy"}}, status_code=409)
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        client.upload(organisation_id=2, file_paths=[f])

        assert len(calls_to(stub_requests, "/_complete")) == 2

    def test_other_409_is_not_retried(self, client, stub_requests, tmp_path):
        f = self._start_s3(stub_requests, tmp_path)
        stub_requests.queue(json_data=error_json("something_else"), status_code=409)
        stub_requests.queue(json_data={"status": "deleted"})

        with pytest.raises(FilesUploadError):
            client.upload(organisation_id=2, file_paths=[f])

        assert len(calls_to(stub_requests, "/_complete")) == 1

    def test_verification_failure_raises_without_cancel(self, client, stub_requests, tmp_path):
        f = self._start_s3(stub_requests, tmp_path)
        stub_requests.queue(json_data=error_json("upload_verification_failed"), status_code=422)

        with pytest.raises(FilesUploadError) as excinfo:
            client.upload(organisation_id=2, file_paths=[f])

        assert excinfo.value.response.status_code == 422
        assert [c for c in stub_requests.calls if c.method == "DELETE"] == []

    def test_gcs_upload_incomplete_resumes_before_retrying(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50, part_size=100))
        stub_requests.queue(json_data=gcs_session(100), status_code=201)
        stub_requests.queue(status_code=200)  # whole file in one chunk
        stub_requests.queue(json_data=error_json("upload_incomplete"), status_code=409)
        stub_requests.queue(status_code=308, headers={"Range": "bytes=0-59"})  # status query
        stub_requests.queue(status_code=200)  # bytes 60-99
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        client.upload(organisation_id=2, file_paths=[f])

        puts = bucket_puts(stub_requests)
        assert [c.kwargs["headers"]["Content-Range"] for c in puts] == [
            "bytes 0-99/100",
            "bytes */100",
            "bytes 60-99/100",
        ]
        assert len(calls_to(stub_requests, "/_complete")) == 2

    def test_unsupported_strategy_fails_and_cancels(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50))
        stub_requests.queue(json_data={**gcs_session(100), "strategy": "azure_blocks"}, status_code=201)
        stub_requests.queue(json_data={"status": "deleted"})

        with pytest.raises(FilesUploadError, match="unsupported upload strategy"):
            client.upload(organisation_id=2, file_paths=[f])

        assert stub_requests.calls[-1].method == "DELETE"


class TestFallbacks:
    def test_limits_failure_falls_back_to_proxied_then_session_on_413(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(status_code=500)  # _limits
        stub_requests.queue(
            json_data={
                "error": {
                    "type": "PAYLOAD_TOO_LARGE",
                    "message": "too large",
                    "details": {"code": "use_signed_upload", "limits": limits_json(threshold=50)},
                }
            },
            status_code=413,
        )
        stub_requests.queue(json_data=s3_session(100, 100), status_code=201)
        stub_requests.queue()
        stub_requests.queue(json_data=entry_json("big.bin", 100))

        result = client.upload(organisation_id=2, file_paths=[f])

        assert [c.url.rsplit("/api/files", 1)[-1] for c in stub_requests.calls if c.method != "PUT"] == [
            "/_limits",
            "/_upload",
            "/_upload-session",
            "/_upload-session/fid-1/_complete",
        ]
        assert result.uploaded[0].name == "big.bin"

    def test_limits_failure_uploads_each_small_file_alone(self, client, stub_requests, tmp_path):
        a = write_file(tmp_path, "a.txt", 5)
        b = write_file(tmp_path, "b.txt", 5)
        stub_requests.queue(status_code=503)
        stub_requests.queue(json_data={"uploaded": [entry_json("a.txt", 5)]})
        stub_requests.queue(json_data={"uploaded": [entry_json("b.txt", 5)]})

        result = client.upload(organisation_id=2, file_paths=[a, b])

        assert len(calls_to(stub_requests, "/_upload")) == 2
        assert [e.name for e in result.uploaded] == ["a.txt", "b.txt"]

    def test_use_proxied_upload_sends_the_file_to_upload(self, client, stub_requests, tmp_path):
        f = write_file(tmp_path, "mid.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50))  # stale threshold
        stub_requests.queue(json_data=error_json("use_proxied_upload"), status_code=400)
        stub_requests.queue(json_data={"uploaded": [entry_json("mid.bin", 100)]})

        result = client.upload(organisation_id=2, file_paths=[f])

        assert stub_requests.calls[-1].url == "http://cp.test/api/files/_upload"
        assert result.uploaded[0].name == "mid.bin"
        assert [c for c in stub_requests.calls if c.method == "DELETE"] == []


class TestPartialFailure:
    def test_first_file_kept_when_second_fails(self, client, stub_requests, tmp_path):
        small = write_file(tmp_path, "small.txt", 10)
        big = write_file(tmp_path, "big.bin", 100)
        stub_requests.queue(json_data=limits_json(threshold=50))
        stub_requests.queue(json_data={"uploaded": [entry_json("small.txt", 10)]})
        stub_requests.queue(json_data={"error": {"type": "TOO_MANY_REQUESTS", "message": "slow down"}}, status_code=429)

        with pytest.raises(requests.HTTPError) as excinfo:
            client.upload(organisation_id=2, file_paths=[small, big])

        err = excinfo.value
        assert isinstance(err, FilesUploadError)
        assert [e.name for e in err.uploaded] == ["small.txt"]
        assert isinstance(err.__cause__, requests.HTTPError)
        assert err.response.status_code == 429
        assert "big.bin" in str(err)
        assert [c for c in stub_requests.calls if c.method == "DELETE"] == []  # initiate never succeeded


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
