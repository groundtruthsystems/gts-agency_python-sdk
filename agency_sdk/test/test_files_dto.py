"""DTO deserialisation tests for the files delegate.

JSON samples are transcribed from the server DTOs in
gts-agency-control/src/service/tenant_files/tenant_files_dto.rs.
"""

from agency_sdk.delegates.files_dto import (
    FileEntry,
    FilesPagedResult,
    SignedUrlResponse,
    UploadResult,
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
