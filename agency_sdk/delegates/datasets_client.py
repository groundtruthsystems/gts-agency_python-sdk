"""Datasets delegate: reads, clone, and the draft -> push -> publish write lifecycle.

The write lifecycle shares the ``.agency/`` workspace layout with ``gts-cli`` so a tree
cloned by either tool can be pushed by the other:

- ``.agency/dataset.json`` — the version GET body minus ``audit``; ``status`` 0 = draft, 1 = published.
- ``.agency/files.json`` — ``{relative/path: {hash: <sha256 hex>, last_modified: <unix secs>}}``.

The three write endpoints name the organisation differently and that is mirrored, not
normalised: dataset commands read body ``organisation_id``, upload reads query ``o``, and the
filesystem ``del`` command reads body ``organisation``. See ``docs/datasets.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.datasets_dto import (
    DatasetDraftResult,
    DatasetFilesResponse,
    DatasetManifestEntry,
    DatasetPublishResult,
    DatasetPushResult,
    DatasetResponse,
    DatasetsPagedResult,
    SignedUrlResponse,
)

_AGENCY_DIR = ".agency"
_DATASET_JSON = "dataset.json"
_FILES_JSON = "files.json"

#: The server refuses to delete these (HTTP 200, ``success: false``); refuse locally first.
_PROTECTED_FILES = frozenset({"metadata.json", "README.md"})

#: The upload route rejects bodies over 100 MiB; keep headroom for the multipart framing.
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024 - 64 * 1024

#: The one ``del`` refusal that means "already gone" — a retried push must not wedge on it.
_ALREADY_DELETED = re.compile(r"^Path '.*' not found$")

_DRAFT_STATUS = 0
_PUBLISHED_STATUS = 1

#: Spelled outside the class: its `list` method shadows the builtin inside the class body.
_RelPaths = list[str]


class DatasetCommandError(RuntimeError):
    """A dataset call returned HTTP 2xx with ``success: false``. HTTP errors still raise ``HTTPError``."""


def _check_success(body: dict[str, Any]) -> dict[str, Any]:
    if body.get("success") is False:
        raise DatasetCommandError(body.get("message") or str(body))
    return body


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entry(path: Path) -> DatasetManifestEntry:
    return DatasetManifestEntry(hash=_sha256_file(path), last_modified=str(int(path.stat().st_mtime)))


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _validate_remote_name(name: str) -> None:
    """Mirror the server's upload filename rule so a bad name fails before any call."""
    if not name or not name[0].isalnum() or ".." in name or "//" in name or "\\" in name:
        raise ValueError(f"Invalid dataset filename {name!r}: must start alphanumeric, no '..', '//' or '\\'")


def _scan_tree(root: Path) -> dict[str, Path]:
    """Map tree-relative POSIX path -> file, skipping the root ``.agency`` dir and symlinks."""
    files: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        if current == root and _AGENCY_DIR in dirnames:
            dirnames.remove(_AGENCY_DIR)
        for filename in filenames:
            path = current / filename
            if path.is_symlink() or not path.is_file():
                continue
            files[path.relative_to(root).as_posix()] = path
    return files


def _update_local_dataset(root: str | Path | None, dataset_id: str, **fields: Any) -> None:
    """Patch ``.agency/dataset.json`` in place when it belongs to ``dataset_id``; otherwise do nothing."""
    if root is None:
        return
    path = Path(root) / _AGENCY_DIR / _DATASET_JSON
    if not path.is_file():
        return
    local = _read_json(path)
    if local.get("id") != dataset_id:
        return
    local.update(fields)
    _write_json(path, local)


class AgencyDatasetsClient(BaseDelegateClient):
    api_path = "/api/datasets"

    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> DatasetsPagedResult:
        """List all datasets for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return DatasetsPagedResult(**self._make_request("GET", "", params=params))

    def get(self, dataset_id: str, organisation_id: int, version: str = "latest") -> DatasetResponse:
        """Get a specific dataset by ID.

        ``version`` is ``latest`` (newest *published*), ``draft`` (the status-0 row), ``last``
        (newest of any status) or a version number.
        """
        params = {"o": str(organisation_id), "v": version}
        return DatasetResponse(**self._make_request("GET", f"/{dataset_id}", params=params))

    def filesystem_list(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        path: str | None = None,
    ) -> DatasetFilesResponse:
        """List files and directories in a dataset filesystem."""
        params: dict[str, str] = {"o": str(organisation_id), "v": version}
        if path is not None:
            params["path"] = path
        return DatasetFilesResponse(**self._make_request("GET", f"/{dataset_id}/filesystem", params=params))

    def filesystem_signed_url(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        file_path: str,
    ) -> SignedUrlResponse:
        """Get a signed URL for downloading a dataset file."""
        params = {"o": str(organisation_id), "v": version}
        data = {
            "file_path": f"/{file_path}",
            "version": f"{version}",
        }
        return SignedUrlResponse(
            **self._make_request("POST", f"/{dataset_id}/filesystem/_signed-url", data=data, params=params)
        )

    def clone_dataset(
        self,
        dataset_id: str,
        organisation_id: int,
        target_path: str,
        version: str = "latest",
    ) -> None:
        """Clone a dataset to a given directory.

        The process is:
        1. Get a dataset.
        2. Using the version from the get response call filesystem_list.
        3. Using that response get signed urls for all files and recursively
           call filesystem_list for directories.
        4. With all the signed urls download the files to the target directory
           and preserve the directory structure.
        5. Write the ``.agency/`` workspace (``dataset.json`` + ``files.json``) so the
           clone can be pushed by :meth:`push` or ``gts-cli``.
        """
        dataset = self.get(dataset_id, organisation_id, version)
        actual_version = str(dataset.version)

        target_dir = Path(target_path)
        target_dir.mkdir(parents=True, exist_ok=True)

        downloaded: _RelPaths = []
        self._clone_directory(dataset_id, organisation_id, actual_version, "", target_dir, downloaded)
        self._write_workspace(target_dir, dataset, downloaded)

    def _write_workspace(self, target_dir: Path, dataset: DatasetResponse, downloaded: _RelPaths) -> None:
        """Write ``.agency/`` for a fresh clone.

        The manifest covers only what was downloaded: files already in the target are left
        out, so a later push sees them as new rather than silently treating them as synced.
        """
        agency_dir = target_dir / _AGENCY_DIR
        agency_dir.mkdir(exist_ok=True)
        _write_json(agency_dir / _DATASET_JSON, dataset.model_dump(mode="json", exclude={"audit"}))
        manifest = {rel: _manifest_entry(target_dir / rel).model_dump() for rel in sorted(downloaded)}
        _write_json(agency_dir / _FILES_JSON, manifest)

    def _clone_directory(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        path: str,
        target_dir: Path,
        downloaded: _RelPaths,
    ) -> None:
        """Recursively clone a directory and its contents, recording each downloaded relative path."""
        files_response = self.filesystem_list(dataset_id, organisation_id, version, path if path else None)

        for item in files_response.items:
            if not path and item.name == _AGENCY_DIR:
                continue  # never let remote content overwrite the local workspace
            item_target_path = target_dir / item.name
            sub_path = f"{path}/{item.name}" if path else item.name

            if item.item_type == "directory":
                item_target_path.mkdir(exist_ok=True)
                self._clone_directory(dataset_id, organisation_id, version, sub_path, item_target_path, downloaded)

            elif item.item_type == "file":
                signed_url_response = self.filesystem_signed_url(dataset_id, organisation_id, version, item.path)
                self._download_file(signed_url_response.signed_url, item_target_path)
                downloaded.append(sub_path)

    def _download_file(self, signed_url: str, target_path: Path) -> None:
        """Download a file from a signed URL to the target path."""
        response = requests.get(signed_url, timeout=60)
        response.raise_for_status()
        target_path.write_bytes(response.content)

    # ------------------------------------------------------------------ write lifecycle

    def draft(self, dataset_id: str, organisation_id: int, root: str | Path | None = None) -> DatasetDraftResult:
        """Open the next mutable version.

        The server inserts ``MAX(version)+1`` as a draft and copies every object from the
        latest version (of any status) into it. Local files are not uploaded.

        Args:
            root: Optional clone root. When its ``.agency/dataset.json`` is for this dataset, its
                ``version``/``status``/``comment`` are updated so a following :meth:`push` is allowed.

        Raises:
            requests.HTTPError: 409 when a draft already exists (one draft at a time).
            DatasetCommandError: HTTP 2xx with ``success: false``.
        """
        body = {"organisation_id": organisation_id, "command": "draft", "payload": {}}
        response = _check_success(
            self._make_request("POST", f"/{dataset_id}/_command", data=body, params={"o": str(organisation_id)})
        )
        result = DatasetDraftResult(**response)
        _update_local_dataset(
            root, dataset_id, version=result.data.version, status=_DRAFT_STATUS, comment="Draft version"
        )
        return result

    def publish(
        self, dataset_id: str, organisation_id: int, comment: str, root: str | Path | None = None
    ) -> DatasetPublishResult:
        """Freeze the current draft in place (status 0 -> 1). Objects are not copied or hashed.

        ``comment`` is sent for parity with ``gts-cli`` but **the server does not store it** — the
        version keeps the comment written when the draft was created. Read the version back to
        show a comment; never echo this argument as if it had been saved.

        Args:
            root: Optional clone root. When it is for this dataset, ``status``/``version`` are updated
                so a later :meth:`push` refuses until the next :meth:`draft`.

        Raises:
            requests.HTTPError: 400 when the latest version is not a draft (a second publish is an
                error, not a no-op); 404 when the dataset is not visible.
            DatasetCommandError: HTTP 2xx with ``success: false``.
        """
        body = {"organisation_id": organisation_id, "command": "publish", "payload": {"comment": comment}}
        response = _check_success(
            self._make_request("POST", f"/{dataset_id}/_command", data=body, params={"o": str(organisation_id)})
        )
        result = DatasetPublishResult(**response)
        _update_local_dataset(root, dataset_id, version=result.data.version, status=_PUBLISHED_STATUS)
        return result

    def push(self, organisation_id: int, root: str | Path, dataset_id: str | None = None) -> DatasetPushResult:
        """Upload a clone's changes into the dataset's current draft.

        Diffs the tree against ``.agency/files.json`` by SHA-256: new and modified files are
        uploaded (overwriting), then files missing from disk are deleted, then the manifest is
        rewritten. Every local check (draft status, filenames, size cap, protected deletes) runs
        before the first call so a batch never stops halfway on a predictable refusal. The
        manifest is only rewritten once every upload and delete succeeded, so a failed push is
        safe to re-run. No changes means no calls and no manifest rewrite.

        Args:
            root: Clone root containing ``.agency/``.
            dataset_id: Optional guard; must equal ``.agency/dataset.json``'s ``id``.

        Raises:
            ValueError: no workspace, id mismatch, local status not draft, a bad filename, a file
                over the upload cap, a delete of ``metadata.json``/``README.md``, or no draft on the server.
            requests.HTTPError: an upload/delete failed at the HTTP level.
            DatasetCommandError: an upload/delete returned ``success: false``.
        """
        root_dir = Path(root)
        agency_dir = root_dir / _AGENCY_DIR
        dataset_json = agency_dir / _DATASET_JSON
        if not dataset_json.is_file():
            raise ValueError(f"{dataset_json} not found; push needs a cloned dataset tree")
        local = _read_json(dataset_json)
        local_id = local.get("id")
        if not isinstance(local_id, str) or not local_id:
            raise ValueError(f"{dataset_json} has no dataset id")
        if dataset_id is not None and dataset_id != local_id:
            raise ValueError(f"dataset_id {dataset_id!r} does not match this tree's dataset {local_id!r}")
        if local.get("status") != _DRAFT_STATUS:
            raise ValueError(f"Dataset {local_id} is not a draft locally (status={local.get('status')!r}); run draft")

        files_json = agency_dir / _FILES_JSON
        previous: dict[str, Any] = _read_json(files_json) if files_json.is_file() else {}
        current = _scan_tree(root_dir)
        entries = {rel: _manifest_entry(path) for rel, path in current.items()}

        uploads = sorted(rel for rel, entry in entries.items() if (previous.get(rel) or {}).get("hash") != entry.hash)
        deletes = sorted(rel for rel in previous if rel not in current)
        unchanged = sorted(set(entries) - set(uploads))
        if not uploads and not deletes:
            return DatasetPushResult(dataset_id=local_id, version=int(local.get("version", 0)), unchanged=unchanged)

        for rel in uploads:
            _validate_remote_name(rel)
            if current[rel].stat().st_size >= _MAX_UPLOAD_BYTES:
                raise ValueError(f"{rel} is over the {_MAX_UPLOAD_BYTES} byte upload limit")
        protected = [rel for rel in deletes if rel in _PROTECTED_FILES]
        if protected:
            raise ValueError(f"Cannot delete protected files: {', '.join(protected)}")

        draft_version = self._require_server_draft(local_id, organisation_id)

        for rel in uploads:
            self._upload_file(local_id, organisation_id, rel, current[rel], entries[rel].hash)
        for rel in deletes:
            self._delete_path(local_id, organisation_id, rel)

        _write_json(files_json, {rel: entry.model_dump() for rel, entry in sorted(entries.items())})
        return DatasetPushResult(
            dataset_id=local_id, version=draft_version, uploaded=uploads, deleted=deletes, unchanged=unchanged
        )

    def _require_server_draft(self, dataset_id: str, organisation_id: int) -> int:
        """Confirm a draft exists server-side; ``del`` with no draft is a server panic (500), not an envelope."""
        try:
            return self.get(dataset_id, organisation_id, "draft").version
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                raise ValueError(f"Dataset {dataset_id} has no draft version on the server; run draft") from error
            raise

    def _upload_file(self, dataset_id: str, organisation_id: int, rel: str, path: Path, checksum: str) -> None:
        """PUT one file into the current draft.

        ``requests`` encodes ``data`` fields before ``files``, so ``filename`` and ``checksum`` reach the
        server before the ``file`` part — required, since it only honours parts parsed before ``file``.
        No ``state`` part: push must overwrite.
        """
        with path.open("rb") as handle:
            response = requests.request(
                "PUT",
                f"{self.base_url}{self.api_path}/{dataset_id}/filesystem/_upload",
                headers={"Authorization": f"Bearer {self.token_supplier.bearer_token()}"},
                params={"o": str(organisation_id)},
                data={"filename": rel, "checksum": checksum},
                files={"file": (rel, handle)},
                timeout=300,
            )
        response.raise_for_status()
        _check_success(response.json() if response.content else {})

    def _delete_path(self, dataset_id: str, organisation_id: int, rel: str) -> None:
        """Delete one path from the current draft; ``Path '…' not found`` counts as already deleted."""
        body = {"organisation": organisation_id, "command": "del", "payload": {"path": rel, "recurse": False}}
        response = self._make_request("POST", f"/{dataset_id}/filesystem/_command", data=body)
        if response.get("success") is False and _ALREADY_DELETED.match(str(response.get("message", ""))):
            return
        _check_success(response)
