#!/usr/bin/env python3
"""Files API lifecycle example: folder, upload, signed URL, gtsf://, download, delete.

Uploads a ~500 KiB file (proxied through comand) and a ~3 MiB file, which is over the
default 1 MiB threshold and so goes through an upload session straight to the bucket.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Cleanup runs unconditionally with a unique folder name, so reruns are
idempotent and leave no residue in the target environment.
"""

import hashlib
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
    payloads = {"small.bin": os.urandom(500 * 1024), "large.bin": os.urandom(3 * 1024 * 1024)}
    uploaded_ids: list[str] = []
    folder_created = False
    ok = False

    with tempfile.TemporaryDirectory() as tmp:
        sources = []
        for name, payload in payloads.items():
            source = Path(tmp) / name
            source.write_bytes(payload)
            sources.append(source)

        try:
            # 1. Baseline listing (root).
            root = files.list(organisation_id=organisation_id)
            print(f"1. root listing: {root.page.total} entries")

            # 2. Create a unique folder.
            entry = files.create_folder(organisation_id=organisation_id, name=folder)
            folder_created = True
            assert entry.is_folder and entry.name == folder
            print(f"2. created folder '{folder}'")

            # 3. Upload both files into it (the large one routes through an upload session).
            limits = files.limits(organisation_id=organisation_id)
            print(f"3. proxied threshold {limits.proxied_upload_max_bytes} bytes, max {limits.max_upload_bytes}")
            result = files.upload(organisation_id=organisation_id, file_paths=sources, path=folder)
            assert [entry.name for entry in result.uploaded] == list(payloads)
            uploaded_ids = [entry.id for entry in result.uploaded]
            for entry in result.uploaded:
                assert entry.size_bytes == len(payloads[entry.name])
                print(f"   uploaded {entry.path} ({entry.size_bytes} bytes, id={entry.id})")
            uploaded = result.uploaded[-1]

            # 4. The folder listing must contain both uploads with the right sizes.
            listing = files.list(organisation_id=organisation_id, path=folder)
            sizes = {item.id: item.size_bytes for item in listing.items}
            for entry in result.uploaded:
                assert sizes.get(entry.id) == len(payloads[entry.name])
            print(f"4. folder listing contains both uploads (total={listing.page.total})")

            # 5. Request a signed URL with a short lifetime.
            signed = files.signed_url(file_id=uploaded.id, organisation_id=organisation_id, expires=120)
            assert signed.file.id == uploaded.id
            print(f"5. signed URL expires at {signed.expires_at}")

            # 6. Resolve the same file through its gtsf:// URI.
            resolved = files.resolve_gtsf_uri(f"gtsf://{uploaded.id}", organisation_id=organisation_id)
            assert resolved.file.id == uploaded.id
            print(f"6. gtsf://{uploaded.id} resolved")

            # 7. Download both and compare SHA-256.
            for entry in result.uploaded:
                target = Path(tmp) / f"downloaded-{entry.name}"
                files.download(file_id=entry.id, organisation_id=organisation_id, target_path=target)
                expected = hashlib.sha256(payloads[entry.name]).hexdigest()
                assert hashlib.sha256(target.read_bytes()).hexdigest() == expected
            print("7. downloaded bytes match the uploads (SHA-256)")

            ok = True
        except Exception:
            traceback.print_exc()
        finally:
            try:
                for uploaded_id in uploaded_ids:
                    files.delete_file(file_id=uploaded_id, organisation_id=organisation_id)
                if uploaded_ids:
                    print(f"8. deleted {len(uploaded_ids)} file(s)")
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
