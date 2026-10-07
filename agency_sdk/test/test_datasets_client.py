import hashlib
import json
import os
from pathlib import Path

import pytest
import requests

from agency_sdk.delegates.datasets_client import AgencyDatasetsClient, DatasetCommandError

BASE = "http://cp.test"
DS_ID = "550e8400-e29b-41d4-a716-446655440000"
ORG = 7


@pytest.fixture
def client(fake_credentials):
    return AgencyDatasetsClient(fake_credentials, base_url=BASE)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dataset_body(version: int = 3, status: int = 0) -> dict:
    return {
        "id": DS_ID,
        "name": "samples",
        "description": "d",
        "tags": [],
        "comment": "c",
        "version": version,
        "status": status,
        "metadata": {},
        "readme": "",
        "audit": {"created_on": "2026-01-01 00:00:00Z"},
    }


def _workspace(root: Path, files: dict[str, bytes], status: int = 0, version: int = 3, manifest=None) -> None:
    """Lay out a clone: files on disk + `.agency/` whose manifest matches them unless overridden."""
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    agency = root / ".agency"
    agency.mkdir(exist_ok=True)
    local = _dataset_body(version, status)
    del local["audit"]
    (agency / "dataset.json").write_text(json.dumps(local))
    if manifest is None:
        manifest = {rel: {"hash": _sha(data), "last_modified": "1"} for rel, data in files.items()}
    (agency / "files.json").write_text(json.dumps(manifest))


def _local(root: Path) -> dict:
    return json.loads((root / ".agency" / "dataset.json").read_text())


def _manifest(root: Path) -> dict:
    return json.loads((root / ".agency" / "files.json").read_text())


def _ok(message: str = "ok", data=None) -> dict:
    return {"success": True, "message": message, "data": data or {}}


# ---------------------------------------------------------------- draft


def test_draft_posts_command_and_updates_matching_workspace(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"}, status=1, version=3)
    manifest_before = _manifest(tmp_path)
    stub_requests.queue(
        {
            "success": True,
            "message": "Draft version 4 created successfully",
            "data": {"dataset_id": DS_ID, "version": 4, "status": "draft", "copied_from_version": 3},
        }
    )

    result = client.draft(DS_ID, ORG, root=tmp_path)

    call = stub_requests.calls[0]
    assert (call.method, call.url) == ("POST", f"{BASE}/api/datasets/{DS_ID}/_command")
    assert call.kwargs["json"] == {"organisation_id": ORG, "command": "draft", "payload": {}}
    assert result.data.version == 4 and result.data.copied_from_version == 3
    local = _local(tmp_path)
    assert (local["version"], local["status"], local["comment"]) == (4, 0, "Draft version")
    assert local["name"] == "samples"  # other keys preserved
    assert _manifest(tmp_path) == manifest_before


def test_draft_leaves_workspace_for_other_dataset_untouched(client, stub_requests, tmp_path):
    _workspace(tmp_path, {}, status=1)
    before = _local(tmp_path)
    stub_requests.queue(_ok(data={"dataset_id": "other", "version": 9, "status": "draft"}))

    client.draft("other", ORG, root=tmp_path)

    assert _local(tmp_path) == before


def test_draft_without_root_makes_one_call(client, stub_requests):
    stub_requests.queue(_ok(data={"dataset_id": DS_ID, "version": 2, "status": "draft"}))
    assert client.draft(DS_ID, ORG).data.version == 2
    assert len(stub_requests.calls) == 1


def test_draft_success_false_raises(client, stub_requests):
    stub_requests.queue({"success": False, "message": "nope"})
    with pytest.raises(DatasetCommandError, match="nope"):
        client.draft(DS_ID, ORG)


def test_draft_conflict_propagates_http_error(client, stub_requests):
    stub_requests.queue({"error": {"type": "INVALID_PARAMS", "message": "A draft version already exists"}}, 409)
    with pytest.raises(requests.HTTPError):
        client.draft(DS_ID, ORG)


# ---------------------------------------------------------------- publish


def test_publish_sends_comment_and_marks_workspace_published(client, stub_requests, tmp_path):
    _workspace(tmp_path, {}, status=0, version=4)
    stub_requests.queue(_ok(data={"dataset_id": DS_ID, "version": 4, "status": "published"}))

    result = client.publish(DS_ID, ORG, "Added Q1", root=tmp_path)

    assert stub_requests.calls[0].kwargs["json"] == {
        "organisation_id": ORG,
        "command": "publish",
        "payload": {"comment": "Added Q1"},
    }
    assert result.data.status == "published"
    local = _local(tmp_path)
    assert (local["status"], local["version"]) == (1, 4)
    assert local["comment"] == "c"  # the comment is not stored server-side, so not locally either


# ---------------------------------------------------------------- push: preconditions


def test_push_refuses_published_tree(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"}, status=1)
    (tmp_path / "b.txt").write_bytes(b"b")
    with pytest.raises(ValueError, match="not a draft"):
        client.push(ORG, tmp_path)
    assert stub_requests.calls == []


def test_push_refuses_mismatched_id(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"})
    with pytest.raises(ValueError, match="does not match"):
        client.push(ORG, tmp_path, dataset_id="someone-else")


def test_push_requires_workspace(client, tmp_path):
    with pytest.raises(ValueError, match="dataset.json"):
        client.push(ORG, tmp_path)


def test_push_no_changes_makes_no_calls_and_keeps_manifest(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a", "train/b.jsonl": b"b"})
    before = (tmp_path / ".agency" / "files.json").read_text()

    result = client.push(ORG, tmp_path)

    assert result.is_empty and result.unchanged == ["a.txt", "train/b.jsonl"]
    assert stub_requests.calls == []
    assert (tmp_path / ".agency" / "files.json").read_text() == before


def test_push_refuses_protected_delete_before_any_call(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"README.md": b"r", "a.txt": b"a"})
    (tmp_path / "README.md").unlink()
    with pytest.raises(ValueError, match="protected"):
        client.push(ORG, tmp_path)
    assert stub_requests.calls == []


def test_push_refuses_invalid_filename_before_any_call(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"})
    (tmp_path / ".gitignore").write_bytes(b"x")
    with pytest.raises(ValueError, match="Invalid dataset filename"):
        client.push(ORG, tmp_path)
    assert stub_requests.calls == []


def test_push_without_server_draft_raises_value_error(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"})
    (tmp_path / "b.txt").write_bytes(b"b")
    stub_requests.queue({"error": {"type": "NOT_FOUND", "message": "Could not find draft"}}, 404)
    with pytest.raises(ValueError, match="no draft"):
        client.push(ORG, tmp_path)
    assert len(stub_requests.calls) == 1


# ---------------------------------------------------------------- push: diff + calls


def test_push_uploads_then_deletes_and_rewrites_manifest(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"keep.txt": b"k", "mod.txt": b"old", "train/gone.jsonl": b"g"})
    (tmp_path / "mod.txt").write_bytes(b"new")
    (tmp_path / "train" / "gone.jsonl").unlink()
    (tmp_path / "train" / "new.jsonl").write_bytes(b"n")
    (tmp_path / ".agency" / "ignored.txt").write_bytes(b"i")
    stub_requests.queue(_dataset_body(version=5, status=0))  # GET v=draft
    stub_requests.queue(_ok(data={"filename": "mod.txt", "size": 3}))
    stub_requests.queue(_ok(data={"filename": "train/new.jsonl", "size": 1}))
    stub_requests.queue(_ok())

    result = client.push(ORG, tmp_path, dataset_id=DS_ID)

    assert result.uploaded == ["mod.txt", "train/new.jsonl"]
    assert result.deleted == ["train/gone.jsonl"]
    assert result.unchanged == ["keep.txt"]
    assert result.version == 5

    get, up1, up2, delete = stub_requests.calls
    assert get.kwargs["params"] == {"o": str(ORG), "v": "draft"}
    assert (up1.method, up1.url) == ("PUT", f"{BASE}/api/datasets/{DS_ID}/filesystem/_upload")
    assert up1.kwargs["params"] == {"o": str(ORG)}
    assert up1.kwargs["data"] == {"filename": "mod.txt", "checksum": _sha(b"new")}
    assert list(up1.kwargs["data"]) == ["filename", "checksum"]
    assert up1.kwargs["files"]["file"][0] == "mod.txt"
    assert "Content-Type" not in up1.kwargs["headers"]
    assert up2.kwargs["data"]["filename"] == "train/new.jsonl"
    assert (delete.method, delete.url) == ("POST", f"{BASE}/api/datasets/{DS_ID}/filesystem/_command")
    assert delete.kwargs["json"] == {
        "organisation": ORG,
        "command": "del",
        "payload": {"path": "train/gone.jsonl", "recurse": False},
    }

    manifest = _manifest(tmp_path)
    assert set(manifest) == {"keep.txt", "mod.txt", "train/new.jsonl"}
    assert manifest["mod.txt"]["hash"] == _sha(b"new")
    assert manifest["mod.txt"]["last_modified"].isdigit()


def test_push_treats_path_not_found_as_deleted(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a", "b.txt": b"b"})
    (tmp_path / "b.txt").unlink()
    stub_requests.queue(_dataset_body())
    stub_requests.queue({"success": False, "message": "Path 'b.txt' not found"})

    assert client.push(ORG, tmp_path).deleted == ["b.txt"]
    assert set(_manifest(tmp_path)) == {"a.txt"}


def test_push_other_delete_refusal_raises_and_keeps_manifest(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a", "b.txt": b"b"})
    before = (tmp_path / ".agency" / "files.json").read_text()
    (tmp_path / "b.txt").unlink()
    stub_requests.queue(_dataset_body())
    stub_requests.queue({"success": False, "message": "No objects found to delete at path 'b.txt'"})

    with pytest.raises(DatasetCommandError, match="No objects found"):
        client.push(ORG, tmp_path)
    assert (tmp_path / ".agency" / "files.json").read_text() == before


def test_push_failed_upload_keeps_manifest(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"})
    before = (tmp_path / ".agency" / "files.json").read_text()
    (tmp_path / "a.txt").write_bytes(b"changed")
    stub_requests.queue(_dataset_body())
    stub_requests.queue({"error": {"type": "INVALID_PARAMS", "message": "Checksum mismatch"}}, 400)

    with pytest.raises(requests.HTTPError):
        client.push(ORG, tmp_path)
    assert (tmp_path / ".agency" / "files.json").read_text() == before


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_push_skips_symlinks(client, stub_requests, tmp_path):
    _workspace(tmp_path, {"a.txt": b"a"})
    (tmp_path / "link.txt").symlink_to(tmp_path / "a.txt")
    assert client.push(ORG, tmp_path).is_empty
    assert stub_requests.calls == []


# ---------------------------------------------------------------- clone


def test_clone_writes_agency_workspace(client, stub_requests, tmp_path):
    stub_requests.queue(_dataset_body(version=2, status=1))
    stub_requests.queue(
        {
            "dataset_id": DS_ID,
            "version": "2",
            "path": "",
            "items": [
                {"name": "a.txt", "type": "file", "path": "a.txt", "last_modified": "x"},
                {"name": "train", "type": "directory", "path": "train", "last_modified": "x"},
            ],
        }
    )
    file_info = {"name": "n", "path": "p", "size": 1, "content_type": "t", "last_modified": "x"}
    stub_requests.queue({"signed_url": "http://s3/a", "expires_at": "x", "file_info": file_info})
    stub_requests.queue(content_bytes=b"AAA")
    stub_requests.queue(
        {
            "dataset_id": DS_ID,
            "version": "2",
            "path": "train",
            "items": [{"name": "b.jsonl", "type": "file", "path": "train/b.jsonl", "last_modified": "x"}],
        }
    )
    stub_requests.queue({"signed_url": "http://s3/b", "expires_at": "x", "file_info": file_info})
    stub_requests.queue(content_bytes=b"BBB")
    (tmp_path / "stray.txt").write_bytes(b"pre-existing")

    client.clone_dataset(DS_ID, ORG, str(tmp_path))

    local = _local(tmp_path)
    assert "audit" not in local
    assert (local["id"], local["version"], local["status"]) == (DS_ID, 2, 1)
    manifest = _manifest(tmp_path)
    assert set(manifest) == {"a.txt", "train/b.jsonl"}  # stray file is not claimed as synced
    assert manifest["a.txt"]["hash"] == _sha(b"AAA")
    assert manifest["train/b.jsonl"]["hash"] == _sha(b"BBB")
