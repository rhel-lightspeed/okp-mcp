"""Shared fixtures for okp-mcp tests."""

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


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ini options for functional tests."""
    parser.addini(
        "functional_max_input_tokens",
        default="40000",
        help="Fail functional tests exceeding this many input tokens (default: 40000)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect functional tests unless explicitly requested with -m functional."""
    if "functional" not in (config.getoption("-m") or ""):
        functional = [item for item in items if "functional" in item.keywords]
        config.hook.pytest_deselected(items=functional)
        items[:] = [item for item in items if "functional" not in item.keywords]


# ---------------------------------------------------------------------------
# Functional test token usage aggregation (xdist-safe)
# ---------------------------------------------------------------------------
# Each functional test records a "token_usage" property via record_property().
# xdist serializes report.user_properties to the controller, so
# pytest_runtest_logreport collects entries from all workers in one place.
# pytest_terminal_summary prints the aggregated table on the controller.
# ---------------------------------------------------------------------------

_functional_token_usage: list[dict] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Collect token_usage properties from functional test reports."""
    if report.when == "call":
        for name, value in report.user_properties:
            if name == "token_usage":
                _functional_token_usage.append(value)  # type: ignore[arg-type]


def pytest_terminal_summary(terminalreporter: object) -> None:
    """Print aggregated functional test token usage summary."""
    if not _functional_token_usage:
        return

    total_in = sum(e["input_tokens"] for e in _functional_token_usage)
    total_out = sum(e["output_tokens"] for e in _functional_token_usage)
    total_req = sum(e["requests"] for e in _functional_token_usage)

    w = terminalreporter.write_line  # type: ignore[union-attr]
    w("")
    w("=" * 78)
    w("FUNCTIONAL TEST TOKEN USAGE SUMMARY")
    w("=" * 78)
    w(f"{'Case':<42} {'Input':>10} {'Output':>10} {'Requests':>10}")
    w("-" * 78)
    for e in sorted(_functional_token_usage, key=lambda x: x["label"]):
        w(f"{e['label']:<42} {e['input_tokens']:>10,} {e['output_tokens']:>10,} {e['requests']:>10}")
    w("-" * 78)
    w(f"{'TOTAL':<42} {total_in:>10,} {total_out:>10,} {total_req:>10}")
    w("=" * 78)
    w("")
