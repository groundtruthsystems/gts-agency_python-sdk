#!/usr/bin/env python3
"""Agent gateway example: chat completions through the org's agentgateway via the openai SDK.

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Read-only against the gateway, so reruns are idempotent.

Environment (defaults target the local stack, gateway on :4000):

    AGENCY_AUTH_URL       Keycloak token endpoint (client-credentials)
    AGENCY_CLIENT_ID      m2m client id
    AGENCY_CLIENT_SECRET  m2m client secret
    AGENCY_ORG_ID         org id, sent as the x-org routing header (string compare)
    GATEWAY_BASE_URL      the gateway's own host (NOT the control-plane API URL)
    GATEWAY_MODEL         virtual-model name from the org's gateway config

The negative check (step 4) proves org scoping is enforced: the same valid JWT
with a wrong ``x-org`` must be rejected with 403.
"""

import asyncio
import os
import sys
import traceback

import openai

from agency_sdk.client import AgencyClient, CredentialsSupplier


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
        # 1. Build the gateway factory off the facade (explicit URL — primary path).
        gateway = client.gateway(org_id=org_id, gateway_base_url=gateway_base_url)
        assert client.gateway(org_id=org_id, gateway_base_url=gateway_base_url) is gateway
        oai = gateway.openai_client(max_retries=0)
        print(f"1. gateway openai client bound to {gateway.gateway_base_url}/v1 (x-org: {org_id}) PASS")

        # 2. Sync completion.
        response = oai.chat.completions.create(
            model=model,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "You are a terse assistant."},
                {"role": "user", "content": "Reply with the single word: pong"},
            ],
        )
        text = response.choices[0].message.content
        assert text and text.strip(), "assistant text must be non-empty"
        print(f"2. openai_client() completion -> {text.strip()!r} PASS")

        # 3. Streaming (openai native SSE).
        deltas = [
            chunk.choices[0].delta.content
            for chunk in oai.chat.completions.create(
                model=model,
                temperature=0.0,
                max_tokens=600,
                stream=True,
                messages=[{"role": "user", "content": "Count from 1 to 3, digits only."}],
            )
            if chunk.choices and chunk.choices[0].delta.content
        ]
        assert deltas, "streaming yielded no deltas"
        print(f"3. streaming -> {len(deltas)} deltas, text={''.join(deltas).strip()!r} PASS")

        # 4. Async completion (the guideline-agent shape).
        async def run_async() -> str:
            aoai = gateway.async_openai_client(max_retries=0)
            reply = await aoai.chat.completions.create(
                model=model,
                temperature=0.0,
                messages=[{"role": "user", "content": "Reply with the single word: pong"}],
            )
            await aoai.close()
            return (reply.choices[0].message.content or "").strip()

        assert asyncio.run(run_async()), "async completion must be non-empty"
        print("4. async_openai_client() completion PASS")

        # 5. Negative: same valid JWT, wrong x-org -> 403 (authz is org-scoped).
        rogue = client.gateway(org_id="999999", gateway_base_url=gateway_base_url).openai_client(max_retries=0)
        try:
            rogue.chat.completions.create(model=model, messages=[{"role": "user", "content": "hi"}])
            raise AssertionError("wrong x-org must be rejected")
        except openai.APIStatusError as error:
            assert error.status_code == 403, f"expected 403 for wrong x-org, got {error.status_code}"
        print("5. wrong x-org rejected with 403 PASS")

        ok = True
    except Exception:
        traceback.print_exc()

    print("ALL STEPS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
