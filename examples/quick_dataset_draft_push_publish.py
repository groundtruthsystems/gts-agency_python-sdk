#!/usr/bin/env python3
"""Dataset write lifecycle example: clone -> draft -> push -> publish.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Each successful run publishes ONE new version of the dataset whose
content equals the previous version: the file it adds is pushed, verified on
the server, and then removed again by a second push before publishing.

If a previous run died after ``draft``, the dataset already has a draft; the
script clones that draft and carries on instead of opening a second one.

Required environment: AGENCY_CLIENT_ID, AGENCY_CLIENT_SECRET, AGENCY_DATASET_ID.
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
    dataset_id = os.getenv("AGENCY_DATASET_ID")
    if not dataset_id:
        print("AGENCY_DATASET_ID is required", file=sys.stderr)
        return 2

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    datasets = AgencyClient(token_supplier=credentials, base_url=base_url).dataset()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "dataset"
        try:
            # 1. Clone the newest version of any status (a leftover draft is cloned as-is).
            datasets.clone_dataset(dataset_id, organisation_id, str(root), version="last")
            cloned = datasets.get(dataset_id, organisation_id, "last")
            assert (root / ".agency" / "dataset.json").is_file() and (root / ".agency" / "files.json").is_file()
            print(f"1. cloned '{cloned.name}' v{cloned.version} (status={cloned.status}) into {root}")

            # 2. Open a draft unless one is already open. Passing root flips the local status to 0.
            if cloned.status == 0:
                draft_version = cloned.version
                print(f"2. draft v{draft_version} already open; reusing it")
            else:
                draft = datasets.draft(dataset_id, organisation_id, root=root)
                draft_version = draft.data.version
                assert draft_version > cloned.version
                print(f"2. {draft.message} (copied from v{draft.data.copied_from_version})")

            # 3. Add a file and push it into the draft.
            rel = f"sdk-example/run-{int(time.time())}.txt"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("written by examples/quick_dataset_draft_push_publish.py\n")
            pushed = datasets.push(organisation_id, root, dataset_id=dataset_id)
            assert pushed.uploaded == [rel] and not pushed.deleted, pushed
            assert pushed.version == draft_version
            print(f"3. pushed {pushed.uploaded} into draft v{pushed.version} ({len(pushed.unchanged)} unchanged)")

            # 4. The server's draft must now hold it.
            listing = datasets.filesystem_list(dataset_id, organisation_id, str(draft_version), "sdk-example")
            assert any(item.name == Path(rel).name for item in listing.items), listing
            print(f"4. server draft lists {rel}")

            # 5. Pushing again with no edits is a no-op (no calls, manifest untouched).
            assert datasets.push(organisation_id, root).is_empty
            print("5. second push: no changes")

            # 6. Remove the file locally and push the delete, so the published content is unchanged.
            (root / rel).unlink()
            removed = datasets.push(organisation_id, root)
            assert removed.deleted == [rel] and not removed.uploaded, removed
            print(f"6. pushed delete of {rel}")

            # 7. Publish. The comment is sent but NOT stored by the server.
            published = datasets.publish(dataset_id, organisation_id, comment="SDK example run", root=root)
            assert published.data.version == draft_version
            latest = datasets.get(dataset_id, organisation_id, "latest")
            assert latest.version == draft_version and latest.status == 1
            print(f"7. {published.message}; stored comment is {latest.comment!r}")

            # 8. The local tree is now published, so push refuses until the next draft.
            try:
                datasets.push(organisation_id, root)
            except ValueError as error:
                print(f"8. push after publish refused: {error}")
            else:
                raise AssertionError("push after publish should have been refused")

            print("OK")
            return 0
        except Exception:
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    sys.exit(main())
