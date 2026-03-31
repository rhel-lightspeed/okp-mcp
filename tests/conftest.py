"""Shared fixtures and hooks for okp-mcp tests."""

from __future__ import annotations

import httpx
import pytest
import respx

from okp_mcp.config import ServerConfig


@pytest.fixture
def sample_solr_response():
    """Realistic Solr JSON response with docs and highlighting."""
    return {
        "responseHeader": {"status": 0, "QTime": 5},
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "allTitle": "Test Document",
                    "title": "Test Document",
                    "view_uri": "/documentation/en-US/Red_Hat_Enterprise_Linux/9/html/test",
                    "documentKind": "documentation",
                    "product": "Red Hat Enterprise Linux",
                    "score": 10.0,
                }
            ],
        },
    }


@pytest.fixture
def empty_solr_response():
    """Solr response with zero results."""
    return {
        "responseHeader": {"status": 0, "QTime": 1},
        "response": {"numFound": 0, "docs": []},
    }


@pytest.fixture
def solr_mock(sample_solr_response):
    """Mock the Solr endpoint using respx."""
    config = ServerConfig()
    with respx.mock:
        route = respx.get(config.solr_endpoint).mock(return_value=httpx.Response(200, json=sample_solr_response))
        yield route


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect functional tests unless explicitly requested with -m functional."""
    if "functional" not in (config.getoption("-m") or ""):
        functional = [item for item in items if "functional" in item.keywords]
        config.hook.pytest_deselected(items=functional)
        items[:] = [item for item in items if "functional" not in item.keywords]


def _collect_token_usage(terminalreporter: pytest.TerminalReporter) -> list[dict[str, int | str]]:
    """Extract token usage properties from test reports across all outcomes."""
    results: list[dict[str, int | str]] = []
    for outcome in ("passed", "failed", "error"):
        for report in terminalreporter.stats.get(outcome, []):
            if report.when != "call":
                continue
            props = dict(report.user_properties)
            if "input_tokens" not in props:
                continue
            results.append(
                {
                    "test_id": report.nodeid,
                    "input_tokens": props["input_tokens"],
                    "output_tokens": props["output_tokens"],
                    "requests": props["requests"],
                    "tool_calls": props["tool_calls"],
                }
            )
    return results


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, exitstatus: int, config: pytest.Config) -> None:
    """Print aggregated token usage from functional tests.

    Token data is attached to test reports via ``record_property`` and flows
    through pytest-xdist's report serialization automatically.  Adding
    ``--junitxml=report.xml`` also embeds the data in the XML artifact.
    """
    results = _collect_token_usage(terminalreporter)
    if not results:
        return

    total_input = sum(int(r["input_tokens"]) for r in results)
    total_output = sum(int(r["output_tokens"]) for r in results)
    total_requests = sum(int(r["requests"]) for r in results)
    total_tool_calls = sum(int(r["tool_calls"]) for r in results)

    terminalreporter.section("Token Usage")
    for r in results:
        total = int(r["input_tokens"]) + int(r["output_tokens"])
        tid = str(r["test_id"])
        if "[" in tid:
            tid = tid.split("[")[-1].rstrip("]")
        terminalreporter.write_line(
            f"  {tid}: {total:,} tokens ({r['input_tokens']:,} in / {r['output_tokens']:,} out), "
            f"{r['requests']} requests, {r['tool_calls']} tool calls"
        )
    terminalreporter.write_line("")
    terminalreporter.write_line(
        f"  Total: {total_input + total_output:,} tokens "
        f"({total_input:,} in / {total_output:,} out), "
        f"{total_requests} requests, {total_tool_calls} tool calls"
    )
