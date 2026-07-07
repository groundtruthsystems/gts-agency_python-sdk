#!/usr/bin/env python3
"""Agent gateway example: OpenAI-compatible chat completion through the org's agentgateway.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Read-only against the gateway (a chat completion), so reruns are
idempotent — nothing to clean up.

Environment (defaults target the local stack, gateway on :4000):

    AGENCY_AUTH_URL       Keycloak token endpoint (client-credentials)
    AGENCY_CLIENT_ID      m2m client id
    AGENCY_CLIENT_SECRET  m2m client secret
    AGENCY_ORG_ID         org id, sent as the x-org routing header (string compare)
    GATEWAY_BASE_URL      the gateway's own host (NOT the control-plane API URL);
                          production or -test run.app URL in deployed environments
    GATEWAY_MODEL         virtual-model name from the org's gateway config
                          (any string routes through a "*" catch-all default)

The negative check (step 4) proves org scoping is enforced: the same valid JWT
with a wrong ``x-org`` must be rejected with 403 (plain-text body). Step 5
exercises native SSE streaming, step 6 the stream=True fast-fail guard, and
step 7 the [openai]-extra helper (skipped when the extra is not installed).
"""

import os
import sys
import traceback

import requests

from agency_sdk.client import AgencyClient, CredentialsSupplier
from agency_sdk.delegates.gateway_client import AgencyGatewayClient
from agency_sdk.delegates.gateway_dto import ChatCompletionRequest, ChatMessage


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    gateway_base_url = os.getenv("GATEWAY_BASE_URL", "http://localhost:4000")
    org_id = os.getenv("AGENCY_ORG_ID", "2")
    model = os.getenv("GATEWAY_MODEL", "biglambda1")

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)

    ok = False
    try:
        # 1. Build the gateway client off the facade (explicit URL — primary path).
        gateway = client.gateway(org_id=org_id, gateway_base_url=gateway_base_url)
        assert client.gateway(org_id=org_id, gateway_base_url=gateway_base_url) is gateway
        print(f"1. gateway client bound to {gateway.gateway_base_url} (x-org: {org_id}) PASS")

        # 2. Convenience path: complete() returns the assistant text.
        text = gateway.complete(
            [
                {"role": "system", "content": "You are a terse assistant."},
                {"role": "user", "content": "Reply with the single word: pong"},
            ],
            model=model,
            temperature=0.0,
        )
        assert text.strip(), "assistant text must be non-empty (raise max_tokens if reasoning ate the budget)"
        print(f"2. complete() -> {text.strip()!r} PASS")

        # 3. Primitive path: chat_completions() exposes the full OpenAI response.
        response = gateway.chat_completions(
            ChatCompletionRequest(
                model=model,
                messages=[ChatMessage(role="user", content="Reply with the single word: pong")],
                temperature=0.0,
            )
        )
        assert response.choices and response.choices[0].message.role == "assistant"
        print(f"3. chat_completions() choices={len(response.choices)} PASS")

        # 4. Negative: same valid JWT, wrong x-org -> 403 (authz is org-scoped).
        rogue = AgencyGatewayClient(token_supplier=credentials, gateway_base_url=gateway_base_url, org_id="999999")
        try:
            rogue.complete([{"role": "user", "content": "hi"}], model=model)
            raise AssertionError("wrong x-org must be rejected")
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            assert status == 403, f"expected 403 for wrong x-org, got {status}"
        print("4. wrong x-org rejected with 403 PASS")

        # 5. Native streaming (zero-dep): SSE deltas via complete_stream().
        deltas = list(
            gateway.complete_stream(
                [{"role": "user", "content": "Count from 1 to 3, digits only."}],
                model=model,
                temperature=0.0,
                max_tokens=600,
            )
        )
        assert deltas, "streaming yielded no deltas"
        print(f"5. complete_stream() -> {len(deltas)} deltas, text={''.join(deltas).strip()!r} PASS")

        # 6. Guard: stream=True on the one-shot methods fails fast (no hang, no network).
        try:
            gateway.complete([{"role": "user", "content": "hi"}], model=model, stream=True)
            raise AssertionError("stream=True must be rejected by the one-shot path")
        except ValueError:
            pass
        print("6. one-shot stream=True rejected with ValueError PASS")

        # 7. Full-feature path via the [openai] extra (skips when not installed).
        try:
            import openai  # noqa: F401
        except ImportError:
            print("7. openai helper SKIP (install the [openai] extra to exercise this step)")
        else:
            oai = gateway.openai_client(max_retries=0)
            response7 = oai.chat.completions.create(
                model=model,
                temperature=0.0,
                messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            )
            content = response7.choices[0].message.content
            assert content and content.strip()
            oai.close()
            print(f"7. openai_client() -> {content.strip()!r} PASS")

        ok = True
    except Exception:
        traceback.print_exc()

    print("ALL STEPS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
