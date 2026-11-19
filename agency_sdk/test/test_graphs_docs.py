"""TS-23, TS-24: the graphs doc carries the paged contract and its security warnings, and its example runs on fakes."""

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


def test_doc_describes_paging():
    """TS-23: page/size usage, the page metadata, server defaults and 0-based pages are documented."""
    text = _doc()
    for needle in ("page=", "size=", "page.total", "page.page", "page.size", "0-based"):
        assert needle in text, needle
    assert re.search(r"defaults?[^\n]*`?page=0`?[^\n]*`?size=10`?", text, re.I)


def test_doc_states_no_snapshot_caveat():
    """TS-23: offset paging has no snapshot, so a catalogue change mid-walk can skip or repeat an item."""
    assert re.search(r"no snapshot[^\n]*(skip|repeat)", _doc(), re.I)


def test_doc_advises_bounded_sizes():
    """TS-23 (T-2): the server has no maximum size, so the doc advises a bounded size."""
    assert re.search(r"no maximum[^\n]*\bsize\b", _doc(), re.I)
    assert re.search(r"bounded `?size`?", _doc(), re.I)


def test_doc_walk_stops_on_total_or_empty_page():
    """TS-23 (data risk): the documented walk stops on ``page.total`` or an empty page, never on len(items) < size."""
    example = _example()
    assert "page.total" in example and "not result.items" in example
    assert "len(result.items) <" not in example


def test_claude_md_lists_graphs_delegate():
    """TS-24."""
    text = (ROOT / "CLAUDE.md").read_text()
    assert "graphs_client.py" in text
    assert "thirteen delegate clients" in text
    graphs_line = next(line for line in text.splitlines() if line.startswith("- `graphs_client.py`"))
    assert "not paged" not in graphs_line
    assert "page" in graphs_line and "size" in graphs_line


def _example() -> str:
    match = re.search(r"<!-- graphs-example -->\n```python\n(.*?)```", _doc(), re.S)
    assert match, "graphs example block not found"
    return match.group(1)


def test_doc_example_runs_against_fakes():
    """TS-23: the walk-all example collects every code exactly once across several pages."""
    graphs = FakeGraphs()
    codes = [f"kg-{i:03d}" for i in range(1, 31)]
    for code in reversed(codes):
        graphs.add_graph(code, enabled=code != "kg-007")

    class _Client:
        def graphs(self):
            return graphs

    namespace = {"client": _Client(), "org_id": 2}
    exec(_example(), namespace)  # nosec B102 - our own doc
    assert namespace["codes"] == codes
    assert namespace["enabled"] == [c for c in codes if c != "kg-007"]
    assert len(graphs.calls) > 1, "the example should walk more than one page"
