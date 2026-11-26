"""DTO deserialisation tests for the files delegate.

JSON samples are transcribed from the server DTOs in
gts-agency-control/src/service/tenant_files/tenant_files_dto.rs.
"""

from agency_sdk.delegates.files_dto import (
    FileEntry,
    FilesPagedResult,
    SignedUrlResponse,
    UploadLimits,
    UploadPartsResponse,
    UploadPartUrl,
    UploadResult,
    UploadSessionResponse,
)

FILE_ENTRY_JSON = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "report.pdf",
    "folder_path": "guidelines/2026",
    "path": "guidelines/2026/report.pdf",
    "is_folder": False,
    "content_type": "application/pdf",
    "size_bytes": 12345,
    "uploaded_by": 7,
    "created_on": "2026-06-10T12:00:00Z",
}

FOLDER_ENTRY_JSON = {
    "id": "f0000000-0000-0000-0000-000000000001",
    "name": "guidelines",
    "folder_path": "",
    "path": "guidelines",
    "is_folder": True,
    "content_type": None,
    "size_bytes": 0,
    "uploaded_by": 7,
    "created_on": "2026-06-09T08:30:00Z",
}


def test_file_entry_deserialises_from_server_json():
    entry = FileEntry(**FILE_ENTRY_JSON)

    assert entry.id == "550e8400-e29b-41d4-a716-446655440000"
    assert entry.name == "report.pdf"
    assert entry.folder_path == "guidelines/2026"
    assert entry.path == "guidelines/2026/report.pdf"
    assert entry.is_folder is False
    assert entry.content_type == "application/pdf"
    assert entry.size_bytes == 12345
    assert entry.uploaded_by == 7
    assert entry.created_on == "2026-06-10T12:00:00Z"


def test_folder_entry_allows_null_content_type_and_root_folder_path():
    entry = FileEntry(**FOLDER_ENTRY_JSON)

    assert entry.is_folder is True
    assert entry.content_type is None
    assert entry.folder_path == ""


def test_files_paged_result_matches_server_paged_shape():
    result = FilesPagedResult(
        **{
            "page": {"page": 0, "size": 50, "total": 2},
            "items": [FILE_ENTRY_JSON, FOLDER_ENTRY_JSON],
        }
    )

    assert result.page.total == 2
    assert result.page.size == 50
    assert len(result.items) == 2
    assert result.items[1].is_folder is True


def test_upload_result_wraps_uploaded_entries():
    result = UploadResult(**{"uploaded": [FILE_ENTRY_JSON]})

    assert len(result.uploaded) == 1
    assert result.uploaded[0].name == "report.pdf"


def test_signed_url_response_carries_file_metadata():
    response = SignedUrlResponse(
        **{
            "signed_url": "https://storage.googleapis.com/files/abc?X-Goog-Signature=sig",
            "expires_at": "2026-06-10T12:15:00Z",
            "file": FILE_ENTRY_JSON,
        }
    )

    assert response.signed_url.startswith("https://storage.googleapis.com/")
    assert response.expires_at == "2026-06-10T12:15:00Z"
    assert response.file.id == FILE_ENTRY_JSON["id"]


LIMITS_JSON = {"proxied_upload_max_bytes": 1048576, "max_upload_bytes": 5368709120, "part_size": 67108864}
PART_URL = "https://bucket.test/obj?partNumber=1&X-Amz-Signature=SECRET-PART"
SESSION_URI = "https://storage.googleapis.com/upload/obj?upload_id=SECRET-SESSION"


def test_upload_limits_matches_server_shape():
    limits = UploadLimits(**LIMITS_JSON)

    assert limits.proxied_upload_max_bytes == 1048576
    assert limits.max_upload_bytes == 5368709120
    assert limits.part_size == 67108864


def test_s3_session_parses_without_gcs_fields():
    # comand omits the strategy-specific fields it does not use (skip_serializing_if), not null
    session = UploadSessionResponse(
        file_id="f-1",
        strategy="s3_multipart",
        part_size=67108864,
        expires_at="2026-11-25T12:00:00Z",
        limits=LIMITS_JSON,
        parts=[{"part_number": 1, "url": PART_URL}],
        parts_expire_at="2026-11-25T11:00:00Z",
    )

    assert session.session_uri is None
    assert session.parts is not None and session.parts[0].url == PART_URL


def test_gcs_session_parses_without_s3_fields():
    session = UploadSessionResponse(
        file_id="f-1",
        strategy="gcs_resumable",
        part_size=67108864,
        expires_at="2026-11-25T12:00:00Z",
        limits=LIMITS_JSON,
        session_uri=SESSION_URI,
    )

    assert session.parts is None and session.parts_expire_at is None


def test_credential_urls_are_hidden_from_repr():
    part = UploadPartUrl(part_number=1, url=PART_URL)
    s3 = UploadSessionResponse(
        file_id="f-1",
        strategy="s3_multipart",
        part_size=1,
        expires_at="x",
        limits=LIMITS_JSON,
        parts=[part],
    )
    gcs = UploadSessionResponse(
        file_id="f-2",
        strategy="gcs_resumable",
        part_size=1,
        expires_at="x",
        limits=LIMITS_JSON,
        session_uri=SESSION_URI,
    )
    parts = UploadPartsResponse(parts=[part], expires_at="x")

    for model in (part, s3, gcs, parts):
        assert "SECRET" not in repr(model)
        assert "SECRET" not in str(model)
    assert "part_number=1" in repr(part)
    assert "file_id='f-2'" in repr(gcs)


def test_validation_errors_do_not_echo_credentials():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError) as excinfo:
        UploadSessionResponse(strategy="gcs_resumable", session_uri=SESSION_URI)
    assert "SECRET" not in str(excinfo.value)

    with pytest.raises(pydantic.ValidationError) as excinfo:
        UploadPartsResponse(parts=[{"url": PART_URL}])
    assert "SECRET" not in str(excinfo.value)
