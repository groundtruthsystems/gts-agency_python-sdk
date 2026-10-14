"""Integration scenarios against a local comand (opt-in: ``pytest -m integration``; see ``docs/local_e2e.md``).

Scenarios: TS-40 contract probe, TS-10 cross-process read-back, TS-20 SIGKILL resume, TS-25 the HITL example,
TS-54 expiry by comand's background job, TS-56 scope isolation, TS-64 first answer wins, TS-65 verified responder.

The probe runs once per session and **skips the whole suite, naming the missing piece**, while the backend
track (``agent_vault_backend_20261012``) has not shipped the contract — such a scenario is recorded as
blocked, never as passed.

Environment: ``AGENCY_AUTH_URL``, ``AGENCY_API_URL``, ``AGENCY_ORG_ID``, ``AGENCY_CLIENT_ID``,
``AGENCY_CLIENT_SECRET``, ``SESSION_TEMPLATE_NAME`` (default ``adk-agent``), and for the human-side scenarios
``AGENCY_HUMAN_TOKEN`` (a reviewer's own access token). Optional: ``AGENT_VAULT_EXPIRY_WAIT`` seconds to wait
for comand's expiry job (default 120).
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

pytest.importorskip("google.adk")

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REQUIRED_ENV = ("AGENCY_AUTH_URL", "AGENCY_API_URL", "AGENCY_CLIENT_ID", "AGENCY_CLIENT_SECRET")


class _StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    def bearer_token(self) -> str:
        return self._token


def _client():
    from agency_sdk.client import AgencyClient, CredentialsSupplier

    credentials = CredentialsSupplier(
        auth_base_url=os.environ["AGENCY_AUTH_URL"],
        client_id=os.environ["AGENCY_CLIENT_ID"],
        client_secret=os.environ["AGENCY_CLIENT_SECRET"],
    )
    return AgencyClient(token_supplier=credentials, base_url=os.environ["AGENCY_API_URL"])


def _org() -> int:
    return int(os.environ.get("AGENCY_ORG_ID", "2"))


def _template() -> str:
    return os.environ.get("SESSION_TEMPLATE_NAME", "adk-agent")


def _human_hitl():
    token = os.environ.get("AGENCY_HUMAN_TOKEN")
    if not token:
        pytest.skip("AGENCY_HUMAN_TOKEN (a reviewer's own token) is not set")
    from agency_sdk.delegates.session_hitl_client import AgencySessionHitlClient

    return AgencySessionHitlClient(token_supplier=_StaticToken(token), base_url=os.environ["AGENCY_API_URL"])


@pytest.fixture(scope="session")
def contract():
    """TS-40: probe each contract item in order; skip everything, naming the first gap."""
    missing_env = [n for n in REQUIRED_ENV if not os.environ.get(n)]
    if missing_env:
        pytest.skip(f"local comand not configured: {', '.join(missing_env)}")
    from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed

    client = _client()
    org = _org()

    def gap(item: str, detail: str) -> None:
        pytest.skip(
            f"comand lacks Agent Vault contract item {item}: {detail} (blocked on agent_vault_backend_20261012)"
        )

    try:
        session_id = client.session_templates().register(org, _template(), correlation_id=f"probe-{uuid.uuid4().hex}")
    except Exception as exc:  # noqa: BLE001 - any failure here is a contract gap
        gap("5", f"register with labels failed ({type(exc).__name__})")
    vault = client.session_vault()
    try:
        first = vault.set(org, session_id, "probe.key", {"a": 1}, if_absent=True)
    except requests.HTTPError as exc:
        gap("2", f"if_absent PUT rejected ({exc.response.status_code if exc.response is not None else '?'})")
    if first.version is None:
        gap("1", "PUT response carries no version")
    try:
        vault.set(org, session_id, "probe.key", {"a": 2}, if_absent=True)
        gap("2", "if_absent did not answer 409 for an existing key")
    except ConditionalWriteFailed as exc:
        if exc.reason != "exists":
            gap("2", f"if_absent answered {exc.reason}")
    try:
        vault.set(org, session_id, "probe.key", {"a": 3}, if_version=first.version + 41)
        gap("2", "if_version did not answer 412 on a mismatch")
    except ConditionalWriteFailed as exc:
        if exc.reason != "version_mismatch":
            gap("2", f"if_version answered {exc.reason}")
    if [e.key for e in vault.list(org, session_id, prefix="probe.").entries] != ["probe.key"]:
        gap("3", "prefix listing")
    try:
        vault.set(org, session_id, "adk.state", {"state": {}}, scope="app")
    except requests.HTTPError:
        gap("4", "scope=app")
    ref = client.sessions().find_by_correlation_id(org, f"never-{uuid.uuid4().hex}")
    if ref is not None:
        gap("5", "lookup of an unknown correlation id returned a session")
    try:
        client.session_hitl().list(org, session_id)
    except requests.HTTPError:
        gap("6", "HITL requests endpoint")
    return client


def _run(cmd, **kw):
    env = {**os.environ, "PYTHONWARNINGS": "ignore"}
    return subprocess.run(cmd, cwd=REPO, env=env, timeout=300, **kw)


def test_cross_process_exact_read_back(contract, tmp_path):
    """TS-10."""
    proc = [sys.executable, "-m", "agency_sdk.test.adk_proc"]
    wrote, read = tmp_path / "w.json", tmp_path / "r.json"
    _run([*proc, "write", "comand", str(wrote)], check=True)
    session_id = json.loads(wrote.read_text())["id"]
    _run([*proc, "read", "comand", session_id, str(read)], check=True)

    assert read.read_text() == wrote.read_text()


def test_sigkilled_run_resumes_without_repeats(contract, tmp_path):
    """TS-20."""
    cid = uuid.uuid4().hex
    counts, out = tmp_path / "counts.json", tmp_path / "out.json"
    cmd = [sys.executable, "-m", "agency_sdk.test.adk_proc", "durable", "comand", str(counts), cid, "4", str(out)]

    assert _run(cmd).returncode == -9
    _run(cmd, check=True)

    assert json.loads(out.read_text())["status"] == "completed"
    assert json.loads(counts.read_text()) == {f"step-{i}": 1 for i in range(1, 6)}


def test_hitl_example_end_to_end(contract):
    """TS-25: run (pause, exit) / answer as a human / resume, as separate processes."""
    _human_hitl()
    env = {**os.environ, "CORRELATION_ID": uuid.uuid4().hex}
    for stage in ("run", "answer", "resume"):
        done = subprocess.run([sys.executable, "examples/quick_durable_hitl.py", stage], cwd=REPO, env=env, timeout=300)
        assert done.returncode == 0, stage


def _paused_run(client, tmp_path, ttl=None):
    import asyncio

    from agency_sdk.adk import DurableRunner, GtsSessionService
    from agency_sdk.test.adk_llm_fakes import CountingTools, call, make_app, text

    tools = CountingTools(tmp_path / f"{uuid.uuid4().hex}.json")
    app = make_app(
        [call("ask_human", "c1", question="ok?"), lambda r: text(f"got {r[-1].response}")], [tools.ask_human()]
    )
    service = GtsSessionService.from_client(client, organisation_id=_org(), template_name=_template())
    runner = DurableRunner(app=app, session_service=service, hitl=client.session_hitl())
    cid = uuid.uuid4().hex
    kwargs = {"hitl_ttl": ttl} if ttl else {}
    outcome = asyncio.run(runner.run(cid, user_id="it-user", message="go", **kwargs))
    assert outcome.status == "awaiting_input"
    return runner, cid, outcome.session_id


def test_expiry_by_comands_background_job(contract, tmp_path):
    """TS-54."""
    import asyncio

    from agency_sdk.adk import errors

    runner, cid, session_id = _paused_run(contract, tmp_path, ttl=timedelta(seconds=2))
    deadline = time.time() + float(os.environ.get("AGENT_VAULT_EXPIRY_WAIT", "120"))
    while time.time() < deadline:
        if contract.session_hitl().get(_org(), session_id, "c1").status == "expired":
            break
        time.sleep(2)
    else:
        pytest.skip("comand's expiry background job did not run within AGENT_VAULT_EXPIRY_WAIT")

    with pytest.raises(errors.HitlExpired):
        asyncio.run(runner.resume(cid, user_id="it-user"))


def test_user_scope_is_isolated_and_fails_closed(contract):
    """TS-56."""
    vault, templates, org = contract.session_vault(), contract.session_templates(), _org()
    a = templates.register(org, _template(), correlation_id=uuid.uuid4().hex, owner="it-user-a")
    b = templates.register(org, _template(), correlation_id=uuid.uuid4().hex, owner="it-user-b")
    unbound = templates.register(org, _template(), correlation_id=uuid.uuid4().hex)
    vault.set(org, a, "adk.state", {"state": {"secret": "a"}}, scope="user")

    with pytest.raises(requests.HTTPError) as miss:
        vault.get(org, b, "adk.state", scope="user")
    assert miss.value.response.status_code == 404
    with pytest.raises(requests.HTTPError) as refused:
        vault.get(org, unbound, "adk.state", scope="user")
    assert 400 <= refused.value.response.status_code < 500 and refused.value.response.status_code != 404


def test_first_answer_wins(contract, tmp_path):
    """TS-64."""
    from agency_sdk.delegates.session_hitl_client import HitlAlreadyAnswered

    human = _human_hitl()
    _, _, session_id = _paused_run(contract, tmp_path)
    human.respond(_org(), session_id, "c1", {"answer": "first"})

    with pytest.raises(HitlAlreadyAnswered):
        human.respond(_org(), session_id, "c1", {"answer": "second"})
    assert contract.session_hitl().get(_org(), session_id, "c1").response == {"answer": "first"}


def test_responder_is_the_human_and_the_worker_is_refused(contract, tmp_path):
    """TS-65."""
    from agency_sdk.delegates.session_hitl_client import HitlNotAuthorised

    human = _human_hitl()
    _, _, session_id = _paused_run(contract, tmp_path)

    with pytest.raises(HitlNotAuthorised):
        contract.session_hitl().respond(_org(), session_id, "c1", "worker answer")
    answered = human.respond(_org(), session_id, "c1", "yes")
    assert answered.responder and answered.responder != os.environ["AGENCY_CLIENT_ID"]
    assert datetime.fromisoformat(answered.answered_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc)
