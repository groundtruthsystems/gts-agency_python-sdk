"""Client for the tenant file storage API (/api/files).

Includes resolution of ``gtsf://<file_id>`` URIs, the convention used in GTS
configurations and rule annotations to reference stored files.

Uploads route by size (see :meth:`AgencyFilesClient.upload`): small files go through
comand's proxied ``_upload`` route, larger ones through an upload session that sends the
bytes straight to the bucket. Presigned part URLs and GCS session URIs are credentials:
they never appear in exception messages, log lines or ``repr``.
"""

import contextlib
import logging
import mimetypes
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, BinaryIO

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.files_dto import (
    FileEntry,
    FilesPagedResult,
    FilesUploadError,
    SignedUrlResponse,
    UploadLimits,
    UploadPartsResponse,
    UploadResult,
    UploadSessionResponse,
)

__all__ = ["AgencyFilesClient", "FilesUploadError", "GTSF_SCHEME"]

logger = logging.getLogger(__name__)

GTSF_SCHEME = "gtsf://"

#: Timeout for the proxied multipart ``_upload`` call (seconds).
_PROXIED_TIMEOUT = 300
#: ``(connect, read)`` timeout for each direct PUT to the bucket.
_DIRECT_TIMEOUT = (10, 300)
_RANGE_RE = re.compile(r"^bytes=0-(\d+)$")

#: One upload step: a proxied batch, or a single file for a session.
_Step = list[tuple[Path, int]]
_Steps = list[_Step]
_Entries = list[FileEntry]
_PartNumbers = list[int]
_Multipart = list[tuple[str, tuple[str, BinaryIO, str | None]]]


def _error_obj(response: requests.Response | None) -> dict[str, Any]:
    """The ``error`` object of comand's standard envelope, or ``{}``."""
    if response is None:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _error_code(response: requests.Response | None) -> str | None:
    """``error.details.code`` from the standard error envelope, if present."""
    details = _error_obj(response).get("details")
    code = details.get("code") if isinstance(details, dict) else None
    return code if isinstance(code, str) else None


def _error_type(response: requests.Response | None) -> str | None:
    """``error.type`` from the standard error envelope (e.g. ``CONFLICT_RETRY``), if present."""
    kind = _error_obj(response).get("type")
    return kind if isinstance(kind, str) else None


def _status(err: requests.HTTPError) -> int | None:
    return err.response.status_code if err.response is not None else None


def _next_gcs_offset(response: requests.Response) -> int:
    """Next byte to send after a GCS ``308``: ``Range: bytes=0-N`` gives ``N+1``; no header gives 0."""
    header = response.headers.get("Range")
    if header is None:
        return 0
    match = _RANGE_RE.match(header.strip())
    if match is None:
        raise requests.HTTPError("GCS resumable upload returned a malformed Range header")
    return int(match.group(1)) + 1


def _direct_put(url: str, data: bytes, headers: dict[str, str]) -> tuple[requests.Response | None, str]:
    """PUT straight to the bucket with no ``Authorization`` header (the URL is the credential).

    Returns the response (``None`` on a transport error) and a URL-free description of the outcome.
    Transport exceptions are swallowed here because their text embeds the presigned URL.
    """
    try:
        response = requests.put(url, data=data, headers=headers, timeout=_DIRECT_TIMEOUT)
    except requests.RequestException as err:
        return None, type(err).__name__
    return response, f"status {response.status_code}"


def _read_exact(handle: BinaryIO, offset: int, length: int, what: str) -> bytes:
    """Read exactly ``length`` bytes at ``offset`` (one part or chunk, never the whole file)."""
    handle.seek(offset)
    chunk = handle.read(length)
    if len(chunk) != length:
        raise IOError(f"File changed during upload: short read for {what}")
    return chunk


class AgencyFilesClient(BaseDelegateClient):
    api_path = "/api/files"

    def __init__(
        self,
        token_supplier: CredentialsSupplier,
        base_url: str = "http://localhost:9003",
        *,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
    ):
        """
        Args:
            max_retries: Retries per S3 part, per run of no-progress GCS attempts, and for
                retryable ``_complete`` conflicts.
            retry_base_delay: Backoff base in seconds; the wait doubles per attempt (0 disables waits).
        """
        super().__init__(token_supplier=token_supplier, base_url=base_url)
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    def list(self, organisation_id: int, path: str = "", page: int = 0, size: int = 50) -> FilesPagedResult:
        """List files and folders at a logical path (folders first, paginated).

        Args:
            organisation_id: The organisation ID.
            path: Directory path to list (default: root, "").
            page: Zero-indexed page number.
            size: Page size (server default 50).
        """
        params = {"o": str(organisation_id), "path": path, "p": str(page), "s": str(size)}
        return FilesPagedResult(**self._make_request("GET", "", params=params))

    def signed_url(self, file_id: str, organisation_id: int, expires: int | None = None) -> SignedUrlResponse:
        """Get a temporary signed download URL for a file.

        Args:
            file_id: The file identifier.
            organisation_id: The organisation ID.
            expires: URL lifetime in seconds. Server default is 900 (15 minutes),
                clamped server-side to [1, 604800] (7 days).

        Raises:
            requests.HTTPError: 404 if the file does not exist, 400 if the id
                refers to a folder.
        """
        params = {"o": str(organisation_id)}
        if expires is not None:
            params["expires"] = str(expires)
        return SignedUrlResponse(**self._make_request("GET", f"/{file_id}/_signed-url", params=params))

    def limits(self, organisation_id: int) -> UploadLimits:
        """Fetch the upload limits ``upload()`` routes by (``GET /api/files/_limits``)."""
        return UploadLimits(**self._make_request("GET", "/_limits", params={"o": str(organisation_id)}))

    def upload(self, organisation_id: int, file_paths: Sequence[str | Path], path: str = "") -> UploadResult:
        """Upload one or more local files to a logical folder, routing each by size.

        The limits come from :meth:`limits`, fetched once per call:

        - Files at or under ``proxied_upload_max_bytes`` (1 MiB by default, at most 30 MiB) go
          through the proxied ``POST /api/files/_upload`` route, grouped into batches whose total
          stays within that threshold.
        - Larger files, up to ``max_upload_bytes`` (5 GiB by default), each go through an upload
          session: the bytes go straight to the bucket (S3 multipart parts or a GCS resumable
          session), then comand verifies the object. A failed session is cancelled best-effort.
        - If ``_limits`` cannot be fetched, each file is tried on ``_upload`` and moved to a
          session when comand answers ``413 use_signed_upload``.

        Files run in ``file_paths`` order and the result lists them in that order. Uploading a
        name that already exists in the folder overwrites it, so retrying a failed call is safe.

        Memory: a proxied batch is built in memory by ``requests`` (peak about twice the batch
        size: 2 MiB at the default threshold). A session holds one part in memory at a time
        (about ``part_size``, 64 MiB by default); the file is never read whole.

        Args:
            organisation_id: The organisation ID.
            file_paths: Local paths of the files to upload.
            path: Destination folder path ("" = root).

        Raises:
            ValueError: Before any network write, if ``file_paths`` is empty, a path is missing
                or not a regular file, or a file exceeds ``max_upload_bytes``.
            FilesUploadError: A ``requests.HTTPError`` subclass raised at the first unrecoverable
                failure. ``uploaded`` holds the entries that already succeeded and ``__cause__``
                is the original error.
        """
        if not file_paths:
            raise ValueError("file_paths must not be empty")
        files: _Step = []
        for raw_path in file_paths:
            file_path = Path(raw_path)
            if not file_path.exists():
                raise ValueError(f"File not found: {file_path}")
            if not file_path.is_file():
                raise ValueError(f"Not a regular file: {file_path}")
            files.append((file_path, file_path.stat().st_size))

        limits: UploadLimits | None
        try:
            limits = self.limits(organisation_id)
        except requests.RequestException:
            limits = None  # fall back to _upload per file, switching to a session on 413
        if limits is not None:
            for file_path, size in files:
                if size > limits.max_upload_bytes:
                    raise ValueError(
                        f"File {file_path} is {size} bytes, over the {limits.max_upload_bytes}-byte upload maximum"
                    )

        uploaded: _Entries = []
        for step in self._plan_upload(files, limits):
            try:
                uploaded.extend(self._run_step(organisation_id, step, path, limits))
            except Exception as err:
                names = ", ".join(file_path.name for file_path, _ in step)
                raise FilesUploadError(
                    f"Upload stopped at {names!r} after {len(uploaded)} file(s) succeeded: {err}",
                    uploaded=uploaded.copy(),
                    response=err.response if isinstance(err, requests.HTTPError) else None,
                ) from err
        return UploadResult(uploaded=uploaded)

    @staticmethod
    def _plan_upload(files: _Step, limits: UploadLimits | None) -> _Steps:
        """Split files, in order, into proxied batches (total within the threshold) and single session files."""
        if limits is None:
            return [[item] for item in files]
        threshold = limits.proxied_upload_max_bytes
        steps: _Steps = []
        batch: _Step = []
        total = 0
        for item in files:
            size = item[1]
            if size > threshold:
                if batch:
                    steps.append(batch)
                    batch, total = [], 0
                steps.append([item])
                continue
            if batch and total + size > threshold:
                steps.append(batch)
                batch, total = [], 0
            batch.append(item)
            total += size
        if batch:
            steps.append(batch)
        return steps

    def _run_step(self, organisation_id: int, step: _Step, folder: str, limits: UploadLimits | None) -> _Entries:
        if limits is None:
            file_path, size = step[0]
            return self._proxied_or_session(organisation_id, file_path, size, folder)
        if step[0][1] <= limits.proxied_upload_max_bytes:
            return self._proxied_upload(organisation_id, [file_path for file_path, _ in step], folder)
        file_path, size = step[0]
        return self._session_upload(organisation_id, file_path, size, folder)

    def _proxied_upload(self, organisation_id: int, file_paths: Sequence[Path], folder: str) -> _Entries:
        """One multipart ``POST /_upload`` with a repeated ``file`` field per file."""
        url = f"{self.base_url}{self.api_path}/_upload"
        params = {"o": str(organisation_id), "path": folder}
        with contextlib.ExitStack() as stack:
            files: _Multipart = []
            for file_path in file_paths:
                handle = stack.enter_context(file_path.open("rb"))
                content_type, _ = mimetypes.guess_type(file_path.name)
                files.append(("file", (file_path.name, handle, content_type)))
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token_supplier.bearer_token()}"},
                params=params,
                files=files,
                timeout=_PROXIED_TIMEOUT,
            )
        response.raise_for_status()
        return UploadResult(**response.json()).uploaded

    def _proxied_or_session(self, organisation_id: int, file_path: Path, size: int, folder: str) -> _Entries:
        """Fallback when ``_limits`` failed: try ``_upload``, switch to a session on ``413 use_signed_upload``."""
        try:
            return self._proxied_upload(organisation_id, [file_path], folder)
        except requests.HTTPError as err:
            if _status(err) == 413 and _error_code(err.response) == "use_signed_upload":
                return self._session_upload(organisation_id, file_path, size, folder, proxied_fallback=False)
            raise

    def _session_upload(
        self, organisation_id: int, file_path: Path, size: int, folder: str, *, proxied_fallback: bool = True
    ) -> _Entries:
        """Upload one file through an upload session: initiate, transfer, complete; cancel on failure."""
        params = {"o": str(organisation_id)}
        content_type, _ = mimetypes.guess_type(file_path.name)
        body = {"folder_path": folder, "filename": file_path.name, "size": size, "content_type": content_type}
        try:
            raw = self._make_request("POST", "/_upload-session", data=body, params=params, retry=False)
        except requests.HTTPError as err:
            if proxied_fallback and _status(err) == 400 and _error_code(err.response) == "use_proxied_upload":
                return self._proxied_upload(organisation_id, [file_path], folder)
            raise
        session = UploadSessionResponse(**raw)

        finished = False
        cancel = True
        try:
            with file_path.open("rb") as handle:
                resume: Callable[[], None] | None = None
                if session.strategy == "s3_multipart":
                    self._s3_transfer(organisation_id, session, handle, size)
                elif session.strategy == "gcs_resumable":
                    self._gcs_transfer(session, handle, size)

                    def resume() -> None:
                        self._gcs_transfer(session, handle, size, query_first=True)

                else:
                    raise ValueError(f"unsupported upload strategy {session.strategy!r}")
                entry = self._complete(organisation_id, session.file_id, resume)
            finished = True
            return [entry]
        except requests.HTTPError as err:
            if _status(err) == 422:
                cancel = False  # upload_verification_failed: comand has already removed the upload
            raise
        finally:
            if not finished and cancel:  # also on KeyboardInterrupt
                self._abort(organisation_id, session.file_id)

    def _s3_transfer(self, organisation_id: int, session: UploadSessionResponse, handle: BinaryIO, size: int) -> None:
        """PUT each S3 part in order, reading one part at a time from disk."""
        part_size = session.part_size
        urls = {part.part_number: part.url for part in session.parts or []}
        part_count = max(1, -(-size // part_size))
        for number in range(1, part_count + 1):
            start = (number - 1) * part_size
            chunk = _read_exact(handle, start, min(part_size, size - start), f"part {number}")
            self._put_s3_part(organisation_id, session.file_id, number, chunk, urls)
            del chunk  # keep the peak at one part

    def _put_s3_part(self, organisation_id: int, file_id: str, number: int, chunk: bytes, urls: dict[int, str]) -> None:
        """PUT one part, with backoff on errors and an immediate re-sign on 403 (which still uses an attempt)."""
        outcome = "no attempt"
        for attempt in range(self.max_retries + 1):
            url = urls.get(number)
            if url is None:
                for part in self._resign_parts(organisation_id, file_id, [number]).parts:
                    urls[part.part_number] = part.url
                url = urls.get(number)
                if url is None:
                    raise requests.HTTPError(f"S3 part {number} was not re-signed by the server")
            response, outcome = _direct_put(url, chunk, {})
            if response is not None and 200 <= response.status_code < 300:
                return
            if response is not None and response.status_code == 403:
                urls.pop(number, None)  # expired or rejected signature: re-sign, no wait
                continue
            if attempt < self.max_retries:
                self._backoff(attempt)
        raise requests.HTTPError(f"S3 part {number} failed after {self.max_retries + 1} attempts ({outcome})")

    def _resign_parts(self, organisation_id: int, file_id: str, part_numbers: _PartNumbers) -> UploadPartsResponse:
        """Re-sign S3 part URLs (``POST …/_parts``)."""
        return UploadPartsResponse(
            **self._make_request(
                "POST",
                f"/_upload-session/{file_id}/_parts",
                data={"part_numbers": part_numbers},
                params={"o": str(organisation_id)},
                retry=True,
            )
        )

    def _gcs_transfer(
        self, session: UploadSessionResponse, handle: BinaryIO, size: int, *, query_first: bool = False
    ) -> None:
        """Send ``part_size`` chunks to the GCS session URI in order, resuming from a status query after errors.

        Fails after ``max_retries`` consecutive attempts that make no progress. ``query_first``
        starts from the status query (used to resume after ``409 upload_incomplete``).
        """
        uri = session.session_uri
        if not uri:
            raise ValueError("gcs_resumable upload session has no session_uri")
        offset = 0
        failures = 0
        query = query_first
        while True:
            if query or offset >= size:
                response, outcome = self._gcs_status(uri, size)
            else:
                end = min(offset + session.part_size, size) - 1
                chunk = _read_exact(handle, offset, end - offset + 1, f"byte offset {offset}")
                response, outcome = _direct_put(uri, chunk, {"Content-Range": f"bytes {offset}-{end}/{size}"})
                del chunk
            resuming, query = query, False
            if response is not None and response.status_code in (200, 201):
                return
            if response is not None and response.status_code == 308:
                next_offset = _next_gcs_offset(response)
                progressed = next_offset > offset
                offset = next_offset
                if progressed:
                    failures = 0
                    continue
                if resuming:
                    continue  # the status query after an error is not an attempt: resend from the reported offset
                failures += 1  # a send that made no progress counts as a failure, but needs no wait
                if failures > self.max_retries:
                    raise requests.HTTPError(f"GCS upload made no progress at byte offset {offset}")
                continue
            failures += 1
            if failures > self.max_retries:
                raise requests.HTTPError(f"GCS upload failed at byte offset {offset} ({outcome})")
            self._backoff(failures - 1)
            query = True

    @staticmethod
    def _gcs_status(uri: str, size: int) -> tuple[requests.Response | None, str]:
        """Ask a GCS resumable session how much it has received (``Content-Range: bytes */size``)."""
        return _direct_put(uri, b"", {"Content-Range": f"bytes */{size}", "Content-Length": "0"})

    def _complete(self, organisation_id: int, file_id: str, resume: Callable[[], None] | None) -> FileEntry:
        """``POST …/_complete``, retrying ``409 upload_incomplete`` and ``409 CONFLICT_RETRY``; 422 is final."""
        for attempt in range(self.max_retries + 1):
            try:
                return FileEntry(
                    **self._make_request(
                        "POST", f"/_upload-session/{file_id}/_complete", params={"o": str(organisation_id)}, retry=False
                    )
                )
            except requests.HTTPError as err:
                incomplete = _error_code(err.response) == "upload_incomplete"
                retryable = _status(err) == 409 and (incomplete or _error_type(err.response) == "CONFLICT_RETRY")
                if not retryable or attempt == self.max_retries:
                    raise
                self._backoff(attempt)
                if incomplete and resume is not None:
                    resume()
        raise AssertionError("unreachable")  # pragma: no cover

    def _abort(self, organisation_id: int, file_id: str) -> None:
        """Best-effort cancel (``DELETE /_upload-session/{id}``); every error is ignored."""
        try:
            self._request("DELETE", f"/_upload-session/{file_id}", params={"o": str(organisation_id)}, retry=False)
        except Exception as err:  # includes 409 upload_already_complete
            logger.debug("Cancelling upload session %s failed: %s", file_id, type(err).__name__)

    def _backoff(self, attempt: int) -> None:
        delay = self.retry_base_delay * (2**attempt)
        if delay > 0:
            time.sleep(delay)

    def create_folder(self, organisation_id: int, name: str, folder_path: str = "") -> FileEntry:
        """Create a virtual folder.

        Args:
            organisation_id: The organisation ID.
            name: Name of the new folder. Must not be empty or contain
                '/', '\\' or '..' (server-validated, 400).
            folder_path: Parent folder path ("" = root).

        Raises:
            requests.HTTPError: 409 if a file or folder with that name already
                exists in the parent folder.
        """
        params = {"o": str(organisation_id)}
        data = {"folder_path": folder_path, "name": name}
        return FileEntry(**self._make_request("POST", "/_folder", data=data, params=params))

    def resolve_gtsf_uri(self, uri: str, organisation_id: int, expires: int | None = None) -> SignedUrlResponse:
        """Resolve a ``gtsf://<file_id>`` URI to a signed download URL plus metadata.

        Args:
            uri: A strict ``gtsf://<file_id>`` URI (lowercase scheme, single
                path-less file id).
            organisation_id: The organisation ID.
            expires: Optional URL lifetime in seconds (see signed_url).

        Raises:
            ValueError: If the URI is malformed; raised before any network call.
        """
        if not uri.startswith(GTSF_SCHEME):
            raise ValueError(f"Not a gtsf:// URI: {uri!r}")
        file_id = uri[len(GTSF_SCHEME) :]
        if not file_id or "/" in file_id:
            raise ValueError(f"Invalid gtsf:// file id: {uri!r}")
        return self.signed_url(file_id=file_id, organisation_id=organisation_id, expires=expires)

    def download(self, file_id: str, organisation_id: int, target_path: str | Path) -> FileEntry:
        """Download a file to a local path via its signed URL (streamed).

        Fetches a signed URL, then streams the blob from object storage in
        1 MiB chunks, so memory stays flat (files can be up to 5 GiB). Parent
        directories are created as needed. After streaming, the number of bytes
        written is verified against the file's known size to catch a silently
        truncated download (e.g. a mid-stream connection drop).

        Returns:
            The downloaded file's metadata.

        Raises:
            IOError: If the number of bytes written does not match the
                expected file size.
        """
        resolved = self.signed_url(file_id=file_id, organisation_id=organisation_id)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(resolved.signed_url, stream=True, timeout=300)
        response.raise_for_status()
        bytes_written = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
                bytes_written += len(chunk)
        if bytes_written != resolved.file.size_bytes:
            raise IOError(
                f"Truncated download for file {file_id}: wrote {bytes_written} bytes, "
                f"expected {resolved.file.size_bytes}"
            )
        return resolved.file

    def delete_file(self, file_id: str, organisation_id: int) -> None:
        """Soft-delete a single file.

        Raises:
            requests.HTTPError: 404 if the file does not exist, 400 if the id
                refers to a folder (use delete_folder instead).
        """
        params = {"o": str(organisation_id)}
        self._make_request("DELETE", f"/{file_id}", params=params)

    def delete_folder(self, organisation_id: int, path: str) -> None:
        """Recursively soft-delete a virtual folder and all its contents.

        Args:
            organisation_id: The organisation ID.
            path: Full path of the folder to delete.
        """
        params = {"o": str(organisation_id), "path": path}
        self._make_request("DELETE", "/_folder", params=params)
