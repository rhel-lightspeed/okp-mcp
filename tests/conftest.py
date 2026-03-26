"""Shared fixtures for okp-mcp tests."""

import httpx
import pytest
import respx

from okp_mcp.config import ServerConfig

# RAG-specific fixtures live in tests/rag/conftest.py.


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
