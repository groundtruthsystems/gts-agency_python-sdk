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

Integration template for other agents
-------------------------------------
The three lines in step (1)+(2) below ARE the adoption pattern - copy them into
your agent and keep them as-is::

    obs = client.observability(service_name, service_version)  # once, at startup
    tracer = obs.init()                                        # once, at startup
    with obs.agent_run("agent.<name>", correlation_id=cid):    # around each run
        ...  # your real work; logs + child spans auto-correlate under one trace

What a real agent does differently from this script (all marked "demo-only" below):

- It already builds an ``AgencyClient`` for the platform API, so it just calls
  ``client.observability(...)`` on that existing client - it does not create a new
  ``CredentialsSupplier``/``AgencyClient`` the way this script does.
- ``service_name``/``service_version`` come from the agent's own identity
  (e.g. ``f"gts-{team}"`` + ``importlib.metadata.version(...)``), not a literal.
- The body of ``agent_run`` is the agent's real run (e.g. ``await workflow.run(...)``,
  LLM calls, rule execution) - not the ``step.compute`` placeholder here.
- Long-running/async agents call ``init()`` once at startup and open one
  ``agent_run`` per request. ``agent_run`` propagates via OTel context (contextvars),
  so child asyncio tasks inherit the root span automatically.
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

    # demo-only: a real agent already has these for its platform API calls and
    # reuses that same AgencyClient below instead of constructing new ones here.
    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)

    # 1. CORE STEP (template): turn observability on, once at startup. Reuses the
    #    client's CredentialsSupplier and defaults the OTLP/Langfuse host to its
    #    base_url. (demo-only: the service name would be the agent's own identity.)
    obs = client.observability("gts-quick-observability")
    tracer = obs.init()
    if tracer is None:
        print("init() returned None - exporter setup failed; continuing untraced.")

    correlation = str(uuid.uuid4())
    ok = False
    try:
        # 2. CORE STEP (template): wrap each agent run in a root span; logs and child
        #    spans created inside nest under it and share its trace id.
        with obs.agent_run("agent.demo", correlation_id=correlation) as span:
            print(f"1. opened root span (correlation_id={correlation})")
            logger.info("doing work inside the run")  # stamped with the trace id

            # demo-only: stand-in for the agent's real work (await workflow.run(...),
            # LLM calls, rule execution, ...). Open child spans like this as needed.
            if tracer is not None:
                with tracer.start_as_current_span("step.compute") as child:
                    child.set_attribute("step.kind", "compute")
                    print("2. nested child span 'step.compute'")

            assert tracer is None or (span is not None and span.is_recording())
        ok = True
    finally:
        obs.shutdown()  # flush the exporters (a long-running agent does this on shutdown)

    print("ALL STEPS PASSED" if ok else "FAILED")
    if tracer is not None:
        print("Verify in Langfuse: one trace 'agent.demo' with a nested 'step.compute' and a correlated log line.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
