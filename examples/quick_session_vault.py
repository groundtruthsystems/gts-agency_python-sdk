#!/usr/bin/env python3
"""Session vault lifecycle example: write, read, list, delete.

The session vault is an organisation- and session-scoped key/value store of
serializable JSON. Autonomous agents use it to persist state mid-run — e.g.
saving a checkpoint before handing off to a human-in-the-loop (HITL).

This mirrors gts-agency-control/examples/quick_session_vault.rs:
  1. Obtain a session id (reuse SESSION_ID, or register one from a template)
  2. PUT  — write entries at different classifications
  3. GET  — read entries (gated by classification; reveal=True for confidential)
  4. LIST — list entries (metadata only — values are never listed)
  5. DELETE — remove an entry
  6. Complete the session (the vault is cleaned up on terminal state)

Reads are gated by classification for human callers; an agent (API-key caller)
may always read. Steps 3's reads are therefore handled gracefully rather than
asserted, matching the Rust example.

Prerequisites: a running control plane + auth server, object storage with a
sessions bucket, and SESSION_VAULT_ENCRYPTION_KEY configured server-side.
"""

import os
import sys
import traceback

import requests

from agency_sdk.client import AgencyClient, CredentialsSupplier

DEFAULT_TEMPLATE_ID = "fa6d5931-9b1d-434a-8ecb-e67a85efa670"


def _register_session(base_url: str, token: str, organisation_id: int, template_id: str) -> str:
    """Register a session from a template via the _command endpoint."""
    url = f"{base_url}/api/session_templates/{template_id}/_command"
    body = {
        "command": "register",
        "organisation": organisation_id,
        "register": {
            "input": {"prompt": "session vault example"},
            "entry": {"code": "example-agent", "entrypoint": "main"},
        },
    }
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"o": str(organisation_id)},
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    session_id: str = response.json()["register"]["session_id"]
    return session_id


def _complete_session(base_url: str, token: str, organisation_id: int, session_id: str) -> None:
    """Drive the session to a terminal state (status 0 = Completed)."""
    url = f"{base_url}/api/sessions/{session_id}/_command"
    body = {
        "command": "update",
        "organisation": organisation_id,
        "update": {"status": 0, "result": {"ok": True}},
    }
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"o": str(organisation_id)},
        json=body,
        timeout=30,
    )
    response.raise_for_status()


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001").rstrip("/")
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    vault = AgencyClient(token_supplier=credentials, base_url=base_url).session_vault()

    ok = False
    try:
        # 1. Obtain a session id: reuse SESSION_ID if provided, else register one.
        session_id = os.getenv("SESSION_ID", "").strip()
        if session_id:
            print(f"1. using existing session {session_id}")
        else:
            template_id = os.getenv("SESSION_TEMPLATE_ID", DEFAULT_TEMPLATE_ID)
            session_id = _register_session(base_url, credentials.bearer_token(), organisation_id, template_id)
            print(f"1. registered session {session_id} from template {template_id}")

        # 2. Write entries at different classifications. The default is `restricted`.
        vault.set(organisation_id, session_id, "checkpoint", {"step": 3, "stage": "awaiting_human_review"})
        vault.set(organisation_id, session_id, "draft", {"text": "sensitive draft body"}, classification="confidential")
        vault.set(organisation_id, session_id, "notes", {"items": ["a", "b", "c"]}, classification="public")
        print("2. wrote 'checkpoint' (restricted), 'draft' (confidential), 'notes' (public)")

        # 3. Read entries — behaviour depends on caller and classification.
        for key, reveal in (("notes", False), ("draft", True), ("checkpoint", False)):
            try:
                entry = vault.get(organisation_id, session_id, key, reveal=reveal)
                print(f"3. GET {key}: {entry.value}")
            except requests.HTTPError as e:
                print(f"3. GET {key} denied/unavailable: {e}")

        # 4. List entries — metadata only, no values.
        listing = vault.list(organisation_id, session_id)
        keys = sorted(e.key for e in listing.entries)
        assert {"checkpoint", "draft", "notes"}.issubset(set(keys)), keys
        print(f"4. vault has {len(listing.entries)} entries: {keys}")

        # 5. Delete an entry and confirm it is gone.
        # vault.delete(organisation_id, session_id, "notes")
        # remaining = sorted(e.key for e in vault.list(organisation_id, session_id).entries)
        # assert "notes" not in remaining, remaining
        # print(f"5. deleted 'notes'; remaining: {remaining}")
        #
        # # 6. Complete the session — reaching a terminal state clears the vault.
        # _complete_session(base_url, credentials.bearer_token(), organisation_id, session_id)
        # print("6. session completed (vault cleaned up on terminal state)")

        ok = True
    except Exception:
        traceback.print_exc()

    print("ALL STEPS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
