#!/usr/bin/env python3
"""Files API lifecycle example: folder, upload, signed URL, gtsf://, download, delete.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Cleanup runs unconditionally with a unique folder name, so reruns are
idempotent and leave no residue in the target environment.
"""

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

from agency_sdk.client import AgencyClient, CredentialsSupplier


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    files = AgencyClient(token_supplier=credentials, base_url=base_url).files()

    folder = f"sdk-e2e-{int(time.time())}"
    payload = os.urandom(1024)
    uploaded_id: str | None = None
    folder_created = False
    ok = False

    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "sample.bin"
        source.write_bytes(payload)

        try:
            # 1. Baseline listing (root).
            root = files.list(organisation_id=organisation_id)
            print(f"1. root listing: {root.page.total} entries")

            # 2. Create a unique folder.
            entry = files.create_folder(organisation_id=organisation_id, name=folder)
            folder_created = True
            assert entry.is_folder and entry.name == folder
            print(f"2. created folder '{folder}'")

            # 3. Upload the sample file into it.
            result = files.upload(organisation_id=organisation_id, file_paths=[source], path=folder)
            assert len(result.uploaded) == 1
            uploaded = result.uploaded[0]
            uploaded_id = uploaded.id
            assert uploaded.size_bytes == len(payload)
            print(f"3. uploaded {uploaded.path} ({uploaded.size_bytes} bytes, id={uploaded.id})")

            # 4. The folder listing must contain the upload.
            listing = files.list(organisation_id=organisation_id, path=folder)
            assert any(item.id == uploaded.id for item in listing.items)
            print(f"4. folder listing contains the upload (total={listing.page.total})")

            # 5. Request a signed URL with a short lifetime.
            signed = files.signed_url(file_id=uploaded.id, organisation_id=organisation_id, expires=120)
            assert signed.file.id == uploaded.id
            print(f"5. signed URL expires at {signed.expires_at}")

            # 6. Resolve the same file through its gtsf:// URI.
            resolved = files.resolve_gtsf_uri(f"gtsf://{uploaded.id}", organisation_id=organisation_id)
            assert resolved.file.id == uploaded.id
            print(f"6. gtsf://{uploaded.id} resolved")

            # 7. Download and compare the bytes.
            target = Path(tmp) / "downloaded.bin"
            files.download(file_id=uploaded.id, organisation_id=organisation_id, target_path=target)
            assert target.read_bytes() == payload
            print("7. downloaded bytes match the upload")

            ok = True
        except Exception:
            traceback.print_exc()
        finally:
            try:
                if uploaded_id is not None:
                    files.delete_file(file_id=uploaded_id, organisation_id=organisation_id)
                    print("8. deleted file")
                if folder_created:
                    files.delete_folder(organisation_id=organisation_id, path=folder)
                    print(f"9. deleted folder '{folder}'")
            except Exception:
                traceback.print_exc()
                ok = False

    print("ALL STEPS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
