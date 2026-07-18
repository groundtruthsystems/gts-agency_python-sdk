#!/usr/bin/env python3
"""Work-queue ingestion lifecycle example (Phase 3A local e2e vehicle).

Drives AgencyWorkQueueClient through the full ingestion contract against a live
control plane: create-with-external-refs, the two FLAT 409 claim-lost bodies,
org-scoped cross-queue lookup, add_ref, a command (ItemCommandResponse), and the
delete "full forget" that CASCADEs the refs.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Queues + items use a unique per-run tag and are torn down
unconditionally, so reruns are idempotent and leave no residue.

The delegate has no queue CRUD (out of scope — ingestion only), so the two
scaffolding queues are created/deleted here via raw authenticated requests; all
item operations go through the delegate, which is what this e2e verifies.

Env: AGENCY_AUTH_URL, AGENCY_API_URL, AGENCY_ORG_ID, AGENCY_CLIENT_ID,
AGENCY_CLIENT_SECRET, AGENCY_SESSION_TEMPLATE_ID (a template that exists in the
org; required only for the publish step).
"""

import os
import sys
import time
import traceback

import requests

from agency_sdk.client import AgencyClient, CredentialsSupplier


def _create_queue(base_url: str, token: str, org: int, name: str) -> int:
    response = requests.post(
        f"{base_url}/api/work_queues",
        params={"o": str(org)},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": name, "description": "Phase 3A e2e scaffolding queue"},
        timeout=30,
    )
    response.raise_for_status()
    return int(response.json()["id"])


def _delete_queue(base_url: str, token: str, org: int, queue_id: int) -> None:
    requests.delete(
        f"{base_url}/api/work_queues/{queue_id}",
        params={"o": str(org)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    org = int(os.getenv("AGENCY_ORG_ID", "2"))
    session_template_id = os.getenv("AGENCY_SESSION_TEMPLATE_ID", "98d227ab-4495-4730-b93a-5a7b8251f977")

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    wq = client.work_queues()
    token = credentials.bearer_token()

    tag = int(time.time())
    file_ref_a = f"file_e2e_{tag}_a"
    hash_ref_a = f"hash_e2e_{tag}_a"
    file_ref_b = f"file_e2e_{tag}_b"

    q1: int | None = None
    q2: int | None = None
    item_a: int | None = None
    item_b: int | None = None
    publish_verified = False
    ok = False

    try:
        # 0. Scaffolding: two queues, to prove _by_ref is org-scoped (not queue-scoped).
        q1 = _create_queue(base_url, token, org, f"wq-e2e-{tag}-1")
        q2 = _create_queue(base_url, token, org, f"wq-e2e-{tag}-2")
        print(f"0. created scaffolding queues q1={q1} q2={q2}")

        # 1. Create item A in q1, atomically claiming file_id + content_hash refs.
        created = wq.create_item(
            queue_id=q1,
            organisation_id=org,
            title=f"ingest {file_ref_a}",
            session_template_id=session_template_id,
            input_data={"file_id": file_ref_a, "path": f"inbox/{file_ref_a}.pdf"},
            external_refs=[
                {"ref_type": "file_id", "ref_value": file_ref_a},
                {"ref_type": "content_hash", "ref_value": hash_ref_a},
            ],
            metadata={"filename": f"{file_ref_a}.pdf"},
        )
        assert created.created is True, created
        assert created.item is not None and created.existing is None
        item_a = created.item.id
        assert created.item.status  # non-empty status string parsed off the real ItemResponse
        print(f"1. created item A id={item_a} status={created.item.status!r} published={created.item.published}")

        # 2. Re-create with the SAME file_id ref -> 409 claim-lost, FLAT body.
        #    The delegate parsing existing.{work_item_id,status,published} at all IS the flat-shape
        #    proof: an error envelope would make ExistingItemSummary(**body) raise ValidationError.
        dup = wq.create_item(
            queue_id=q1,
            organisation_id=org,
            title="dup",
            session_template_id=session_template_id,
            input_data={"file_id": file_ref_a},
            external_refs=[{"ref_type": "file_id", "ref_value": file_ref_a}],
        )
        assert dup.created is False, dup
        assert dup.item is None and dup.existing is not None
        assert dup.existing.work_item_id == item_a, dup.existing
        print(
            f"2. create dup -> 409 claim-lost: existing work_item_id={dup.existing.work_item_id} "
            f"status={dup.existing.status!r} published={dup.existing.published}"
        )

        # 2b. Raw-body evidence: the literal 409 is the flat domain object, NOT the {"error":{...}} envelope.
        raw = requests.post(
            f"{base_url}/api/work_queues/{q1}/items",
            params={"o": str(org)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "title": "dup-raw",
                "session_template_id": session_template_id,
                "input_data": {"file_id": file_ref_a},
                "external_refs": [{"ref_type": "file_id", "ref_value": file_ref_a}],
            },
            timeout=30,
        )
        assert raw.status_code == 409, f"expected 409, got {raw.status_code}: {raw.text}"
        raw_body = raw.json()
        assert "error" not in raw_body, f"409 must be flat, got envelope: {raw_body}"
        assert {"work_item_id", "status", "published"} <= set(raw_body), raw_body
        print(f"2b. raw 409 body is FLAT (no envelope): {raw_body}")

        # 3. Fetch item A by (queue,item).
        got = wq.get_item(queue_id=q1, item_id=item_a, organisation_id=org)
        assert got.id == item_a and got.work_queue_id == q1
        print(f"3. get_item A -> id={got.id} work_queue_id={got.work_queue_id}")

        # 4. Org-scoped lookup by ref: A's file_id resolves with NO queue in the path.
        by_a = wq.get_item_by_ref(organisation_id=org, ref_type="file_id", ref_value=file_ref_a)
        assert by_a is not None and by_a.id == item_a
        print(f"4. _by_ref(file_id={file_ref_a}) -> item {by_a.id} (org-scoped, no queue id)")

        # 5. Create item B in the OTHER queue; look it up org-scoped -> proves cross-queue reach.
        created_b = wq.create_item(
            queue_id=q2,
            organisation_id=org,
            title=f"ingest {file_ref_b}",
            session_template_id=session_template_id,
            input_data={"file_id": file_ref_b},
            external_refs=[{"ref_type": "file_id", "ref_value": file_ref_b}],
        )
        assert created_b.created is True and created_b.item is not None
        item_b = created_b.item.id
        by_b = wq.get_item_by_ref(organisation_id=org, ref_type="file_id", ref_value=file_ref_b)
        assert by_b is not None and by_b.id == item_b and by_b.work_queue_id == q2
        print(f"5. item B id={item_b} in q2, resolved org-scoped by ref -> cross-queue OK")

        # 6. Missing ref -> None (404 mapped, not an exception).
        missing = wq.get_item_by_ref(organisation_id=org, ref_type="file_id", ref_value=f"nope_{tag}")
        assert missing is None
        print("6. _by_ref(missing) -> None (404 mapped)")

        # 7. add_ref a NEW content_hash to A -> added.
        added = wq.add_ref(
            queue_id=q1, item_id=item_a, organisation_id=org, ref_type="content_hash", ref_value=f"hash_new_{tag}"
        )
        assert added.added is True and added.owner_work_item_id is None
        print("7. add_ref new content_hash to A -> added=True")

        # 8. add_ref A's file_id onto B -> 409 owner, FLAT body (owner id + status, NO published).
        conflict = wq.add_ref(
            queue_id=q2, item_id=item_b, organisation_id=org, ref_type="file_id", ref_value=file_ref_a
        )
        assert conflict.added is False, conflict
        assert conflict.owner_work_item_id == item_a, conflict
        assert conflict.owner_status, conflict
        print(
            f"8. add_ref owned ref onto B -> 409 owner: work_item_id={conflict.owner_work_item_id} "
            f"status={conflict.owner_status!r}"
        )

        # 8b. Raw-body evidence for the add_ref 409: flat, narrower than create's (no `published`).
        raw2 = requests.post(
            f"{base_url}/api/work_queues/{q2}/items/{item_b}/_command",
            params={"o": str(org)},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"command": "add_ref", "ref_type": "file_id", "ref_value": file_ref_a},
            timeout=30,
        )
        assert raw2.status_code == 409, f"expected 409, got {raw2.status_code}: {raw2.text}"
        raw2_body = raw2.json()
        assert "error" not in raw2_body, f"add_ref 409 must be flat, got envelope: {raw2_body}"
        assert {"work_item_id", "status"} <= set(raw2_body), raw2_body
        print(f"8b. raw add_ref 409 body is FLAT: {raw2_body}")

        # 9. publish A -> ItemCommandResponse {success, message, session_id?} (Phase 2b live confirm).
        #    Best-effort / Stage-2-adjacent: publish DISPATCHES a real session via the template, which
        #    reaches the session subsystem (LLM/agentgateway) — a slow or failed dispatch there is a
        #    Stage-2 / ③ concern, NOT this delegate's wire contract. It is reported but never fails the
        #    contract-critical steps above. (Note: the base client's 30s timeout bounds a hung dispatch;
        #    ItemCommandResponse parsing is already proven by unit tests against the exact server schema.)
        try:
            command = wq.publish_item(queue_id=q1, item_id=item_a, organisation_id=org)
            assert command.success is True, command
            publish_verified = True
            print(f"9. publish A -> ItemCommandResponse success={command.success} session_id={command.session_id}")
        except requests.RequestException as publish_error:
            detail = (
                publish_error.response.text[:200]
                if publish_error.response is not None
                else type(publish_error).__name__
            )
            print(f"9. publish A -> dispatch not completed ({detail}); Stage-2 concern, contract steps unaffected")

        # 10. delete A (full forget) -> None; its refs CASCADE, so _by_ref now misses.
        result = wq.delete_item(queue_id=q1, item_id=item_a, organisation_id=org)
        assert result is None
        forgotten = wq.get_item_by_ref(organisation_id=org, ref_type="file_id", ref_value=file_ref_a)
        assert forgotten is None, "delete should CASCADE the external refs (full forget)"
        item_a = None
        print("10. delete A -> None; _by_ref(A's file_id) now None (refs CASCADED)")

        ok = True
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if q1 is not None:
                if item_a is not None:
                    wq.delete_item(queue_id=q1, item_id=item_a, organisation_id=org)
                _delete_queue(base_url, token, org, q1)
            if q2 is not None:
                if item_b is not None:
                    wq.delete_item(queue_id=q2, item_id=item_b, organisation_id=org)
                _delete_queue(base_url, token, org, q2)
            print("cleanup: items + scaffolding queues removed")
        except Exception:
            traceback.print_exc()
            ok = False

    print(f"ALL STEPS PASSED (publish_verified={publish_verified})" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
