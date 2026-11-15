"""TS-30: the graphs doc carries its security warnings, and its example runs on the fakes."""

import pathlib
import re

from agency_sdk.test.adk_fakes import FakeGraphs

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "graphs.md"


def _doc() -> str:
    return DOC.read_text()


def test_doc_warns_against_logging_headers_and_config():
    text = _doc()
    assert "exc.response.request.headers" in text
    assert re.search(r"never log[^\n]*`config`", text, re.I)


def test_doc_warns_against_http_base_url():
    assert re.search(r"`http://`[^\n]*base URL", _doc(), re.I)


def test_doc_warns_never_to_send_the_token_to_config_base_url():
    assert re.search(r"never send[^\n]*token[^\n]*`config\.base_url`", _doc(), re.I)


def test_claude_md_lists_graphs_delegate():
    text = (ROOT / "CLAUDE.md").read_text()
    assert "graphs_client.py" in text
    assert "thirteen delegate clients" in text


def test_doc_example_runs_against_fakes():
    match = re.search(r"<!-- graphs-example -->\n```python\n(.*?)```", _doc(), re.S)
    assert match, "graphs example block not found"
    graphs = FakeGraphs()
    graphs.add_graph("medical-ontology", name="Medical Ontology")
    graphs.add_graph("other", enabled=False)

    class _Client:
        def graphs(self):
            return graphs

    namespace = {"client": _Client(), "org_id": 2}
    exec(match.group(1), namespace)  # nosec B102 - our own doc
    assert namespace["enabled"] == ["medical-ontology"]
