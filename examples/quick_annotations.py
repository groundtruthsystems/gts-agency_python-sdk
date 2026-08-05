#!/usr/bin/env python3
"""Annotations push lifecycle example (Gate A live e2e vehicle).

Drives AgencyAnnotationsClient through the whole publish contract against a live
control plane: seed-or-find the job specification, push a rule graph in one call
(create DRAFT -> multipart upload -> read back ACTIVE + total_jobs), push the same
graph from a file, list the batch back, and exercise the two failure modes that
matter — a graph with no target-class vertices (400, leaving an empty DRAFT batch)
and a call with no graph source at all (client-side ValueError, no HTTP).

Self-verifying: every step asserts its outcome and the script exits non-zero on
failure. Batch names carry a unique per-run tag and every batch created is
archived in the teardown, so reruns are idempotent and leave no active residue.

The delegate has no archive command (out of scope — publishing only), so the
teardown archives via a raw authenticated request; everything being verified goes
through the delegate.

Env: AGENCY_AUTH_URL, AGENCY_API_URL, AGENCY_ORG_ID, AGENCY_CLIENT_ID,
AGENCY_CLIENT_SECRET.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import requests

from agency_sdk.client import AgencyClient, CredentialsSupplier
from agency_sdk.delegates.annotations_dto import DEFAULT_JOB_TYPE, BatchStatus

#: Two rule vertices (-> two jobs) plus a document vertex that must NOT become one.
RULE_GRAPH = {
    "run_id": "quick-annotations-e2e",
    "vertices": [
        {
            "bid": "v-rule-1",
            "class": "rule",
            "name": "Knee MRI is indicated after 6 weeks of failed conservative care",
            "metadata": {"term": "knee-mri-indication"},
        },
        {
            "bid": "v-rule-2",
            "class": "rule",
            "name": "Conservative care is first-line for uncomplicated knee pain",
            "metadata": {"term": "conservative-care-first"},
        },
        {"bid": "v-doc-1", "class": "document", "name": "MTUS Knee Guideline 2026"},
    ],
    "edges": [
        {"from": "v-rule-1", "to": "v-doc-1", "label": "sourced_from"},
        {"from": "v-rule-2", "to": "v-doc-1", "label": "sourced_from"},
    ],
}

EXPECTED_JOBS = sum(1 for v in RULE_GRAPH["vertices"] if v["class"] == "rule")

CHECKLIST = [
    {"id": "text_matches_source", "label": "Rule text matches the source"},
    {"id": "page_reference_correct", "label": "Page reference is correct"},
]


def _archive_batch(base_url: str, token: str, org: int, batch_id: str) -> None:
    requests.post(
        f"{base_url}/api/annotations/{batch_id}/_command",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"command": "archive", "organisation": org, "payload": {}},
        timeout=30,
    )


def main() -> int:
    auth_base_url = os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token")
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    org = int(os.getenv("AGENCY_ORG_ID", "2"))

    credentials = CredentialsSupplier(
        auth_base_url=auth_base_url,
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    annotations = client.annotations()
    token = credentials.bearer_token()

    tag = int(time.time())
    created_batches: list[str] = []
    ok = False

    try:
        # 1. Seed-or-find the job specification, so the jobs get a real checklist.
        #    get -> 404 -> create is the documented recipe (nothing enforces code uniqueness).
        try:
            spec = annotations.get_spec(organisation_id=org, code=DEFAULT_JOB_TYPE)
            print(f"1. spec {DEFAULT_JOB_TYPE!r} already seeded: id={spec.id} status={spec.status}")
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code != 404:
                raise
            created_spec = annotations.create_spec(
                organisation_id=org,
                code=DEFAULT_JOB_TYPE,
                name="Rule validation",
                checklist=CHECKLIST,
                instructions="Confirm each rule against its source document.",
            )
            spec = annotations.get_spec(organisation_id=org, code=DEFAULT_JOB_TYPE)
            assert spec.id == created_spec.id, (spec, created_spec)
            print(f"1. spec {DEFAULT_JOB_TYPE!r} created: id={spec.id}")
        assert spec.code == DEFAULT_JOB_TYPE

        # 1b. The spec is listable too (name→code resolution vehicle for callers).
        listed_specs = annotations.list_specs(organisation_id=org, size=100)
        assert any(s.code == DEFAULT_JOB_TYPE for s in listed_specs.items), listed_specs
        print(f"1b. list_specs -> {listed_specs.page.total} spec(s) in org {org}")

        # 2. The one-call push: create DRAFT -> upload -> read back ACTIVE with the job count.
        pushed = annotations.push_graph(
            organisation_id=org,
            name=f"annotations-e2e-{tag}-dict",
            graph=RULE_GRAPH,
            description="quick_annotations.py e2e",
            instructions="Confirm each rule against its source document.",
        )
        created_batches.append(pushed.batch_id)
        assert pushed.status == BatchStatus.ACTIVE, pushed
        assert pushed.total_jobs == EXPECTED_JOBS, f"expected {EXPECTED_JOBS} jobs, got {pushed.total_jobs}"
        assert pushed.batch.target_class == "rule", pushed.batch
        assert pushed.batch.graph_run_id == RULE_GRAPH["run_id"], pushed.batch
        assert pushed.batch.graph_uri, pushed.batch
        print(
            f"2. push_graph(dict) -> batch={pushed.batch_id} status=ACTIVE "
            f"total_jobs={pushed.total_jobs} run_id={pushed.batch.graph_run_id!r}"
        )

        # 3. Read the batch back independently: the count is server state, not a local echo.
        batch = annotations.get_batch(organisation_id=org, batch_id=pushed.batch_id)
        assert batch.total_jobs == EXPECTED_JOBS and batch.status == BatchStatus.ACTIVE, batch
        assert batch.completed_jobs == 0, batch
        print(f"3. get_batch -> {batch.completed_jobs}/{batch.total_jobs} done, context_hops={batch.context_hops}")

        # 4. The same graph from a file: identical outcome, no local staging needed by callers.
        with tempfile.TemporaryDirectory() as workspace:
            graph_file = Path(workspace) / "sandbox_graph.json"
            graph_file.write_text(json.dumps(RULE_GRAPH))
            from_file = annotations.push_graph(
                organisation_id=org, name=f"annotations-e2e-{tag}-file", file_path=graph_file
            )
        created_batches.append(from_file.batch_id)
        assert from_file.total_jobs == EXPECTED_JOBS, from_file
        print(f"4. push_graph(file_path) -> batch={from_file.batch_id} total_jobs={from_file.total_jobs}")

        # 5. The batches are listable (paged read).
        listed = annotations.list_batches(organisation_id=org, size=100)
        listed_ids = {b.id for b in listed.items}
        assert {pushed.batch_id, from_file.batch_id} <= listed_ids, sorted(listed_ids)
        print(f"5. list_batches -> {listed.page.total} batch(es); both e2e batches present")

        # 6. Negative: a graph with no rule vertices is a 400, and the batch stays DRAFT+empty
        #    (the documented residue of a half-completed push).
        empty = annotations.create_batch(organisation_id=org, name=f"annotations-e2e-{tag}-empty")
        created_batches.append(empty.id)
        try:
            annotations.upload_graph(
                organisation_id=org, batch_id=empty.id, graph={"vertices": [{"bid": "v", "class": "document"}]}
            )
            raise AssertionError("expected a 400 for a graph with no rule vertices")
        except requests.HTTPError as error:
            assert error.response is not None and error.response.status_code == 400, error
            print(f"6. upload with no rule vertices -> 400: {error.response.text[:120]}")
        residue = annotations.get_batch(organisation_id=org, batch_id=empty.id)
        assert residue.status == BatchStatus.DRAFT and residue.total_jobs == 0, residue
        print(f"6b. failed push leaves batch {empty.id} DRAFT with 0 jobs (recoverable via list_batches)")

        # 7. Client-side guard: no graph source at all never reaches the network.
        try:
            annotations.push_graph(organisation_id=org, name="never-created")
            raise AssertionError("expected ValueError when neither graph nor file_path is given")
        except ValueError as error:
            print(f"7. push_graph() with no graph source -> ValueError: {error}")

        ok = True
    except Exception:
        traceback.print_exc()
    finally:
        for batch_id in created_batches:
            _archive_batch(base_url, token, org, batch_id)
        if created_batches:
            print(f"cleanup: archived {len(created_batches)} batch(es)")

    print("\nALL STEPS PASSED" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
