#!/usr/bin/env python3
"""Observability tracing example: enable OTLP tracing + log correlation in a few lines.

Requires the optional extra::

    pip install gts-agency-python-sdk[observability]

Emits one trace (root span ``agent.demo`` with a nested ``step.compute`` child)
plus a correlated log line to the configured Langfuse backend, all authenticated
with the same credentials the API client uses. Verify in the Langfuse UI that the
run appears as a single trace whose log line shares the trace id.

Self-verifying locally: it asserts ``agent_run`` yields a recording span (when
tracing is on) and exits non-zero on failure. It cannot assert the signals landed
in Langfuse - confirm that in the UI.
"""

import logging
import os
import sys
import uuid

from agency_sdk.client import AgencyClient, CredentialsSupplier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("quick_observability")


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)

    # 1. Turn observability on. Reuses the client's CredentialsSupplier and defaults
    #    the OTLP/Langfuse host to its base_url.
    obs = client.observability("gts-quick-observability")
    tracer = obs.init()
    if tracer is None:
        print("init() returned None - exporter setup failed; continuing untraced.")

    correlation = str(uuid.uuid4())
    ok = False
    try:
        # 2. Wrap the run in a root span; logs and child spans nest under it.
        with obs.agent_run("agent.demo", correlation_id=correlation) as span:
            print(f"1. opened root span (correlation_id={correlation})")
            logger.info("doing work inside the run")  # stamped with the trace id

            if tracer is not None:
                with tracer.start_as_current_span("step.compute") as child:
                    child.set_attribute("step.kind", "compute")
                    print("2. nested child span 'step.compute'")

            assert tracer is None or (span is not None and span.is_recording())
        ok = True
    finally:
        obs.shutdown()  # flush the exporters

    print("ALL STEPS PASSED" if ok else "FAILED")
    if tracer is not None:
        print("Verify in Langfuse: one trace 'agent.demo' with a nested 'step.compute' and a correlated log line.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
