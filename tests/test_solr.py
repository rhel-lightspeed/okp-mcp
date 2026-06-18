"""Tests for SOLR query and query cleaning behavior."""

# pyright: reportMissingImports=false

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from pydantic import ValidationError

from okp_mcp.config import ServerConfig
from okp_mcp.solr import _clean_query
from okp_mcp.solr import _get_highlight_snippets
from okp_mcp.solr import _solr_query
from okp_mcp.types import SolrResponse


_SOLR_ENDPOINT = ServerConfig().solr_endpoint


async def test_solr_query_uses_provided_client(sample_solr_response):
    """Provided client is used directly for the HTTP call."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        async with httpx.AsyncClient(timeout=30.0) as client:
            data = await _solr_query({"q": "kernel panic"}, client=client, solr_endpoint=_SOLR_ENDPOINT)

    assert route.called
    assert isinstance(data, SolrResponse)
    assert data.response.numFound == sample_solr_response["response"]["numFound"]


async def test_solr_query_timeout_exception_propagates():
    """TimeoutException is re-raised to the caller."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.TimeoutException("slow SOLR"))

    with pytest.raises(httpx.TimeoutException):
        await _solr_query({"q": "timeout"}, client=client, solr_endpoint=_SOLR_ENDPOINT)


async def test_solr_query_http_status_error_propagates():
    """HTTPStatusError is re-raised to the caller."""
    request = httpx.Request("GET", _SOLR_ENDPOINT)
    response = httpx.Response(503, request=request, text="service unavailable")
    status_error = httpx.HTTPStatusError("bad status", request=request, response=response)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=status_error)

    with pytest.raises(httpx.HTTPStatusError):
        await _solr_query({"q": "status"}, client=client, solr_endpoint=_SOLR_ENDPOINT)


async def test_solr_query_respx_regression_guard(solr_mock, sample_solr_response):
    """Existing respx endpoint mocking keeps working after client refactor."""
    async with httpx.AsyncClient() as client:
        data = await _solr_query({"q": "selinux"}, client=client, solr_endpoint=_SOLR_ENDPOINT)

    assert solr_mock.called
    assert data.response.numFound == sample_solr_response["response"]["numFound"]


@pytest.mark.parametrize(
    "input_query, expected",
    [
        ("the red hat enterprise linux", "red hat enterprise linux"),
        ("rpm-ostree update", '"rpm-ostree" update'),
        ("RHEL 9.4 kernel", "RHEL 9.4 kernel"),
        ("the and or", "the and or"),
        ("the and ?", "the and ?"),
        ("", ""),
        ('"exact phrase" kernel', '"exact phrase" kernel'),
        ("Can I run a RHEL 6 container on RHEL 9?", "run RHEL 6 container RHEL 9"),
        ("What version! of RHEL?", "version RHEL"),
        ('"RHEL 9?" support', '"RHEL 9?" support'),
        ("bond ip 192.168.1.1/24 gateway 10.0.0.1", "bond ip gateway"),
        ("configure ens3 and eth0 on RHEL", "configure RHEL"),
    ],
    ids=[
        "stopwords",
        "hyphenated",
        "numeric",
        "all-stopwords",
        "punctuation-only",
        "empty",
        "quoted-phrase",
        "trailing-question-mark",
        "trailing-exclamation-and-question",
        "quoted-punctuation",
        "ip-cidr-stripped",
        "nic-names-stripped",
    ],
)
def test_clean_query(input_query, expected):
    """_clean_query strips trailing Solr wildcard chars from output tokens."""
    assert _clean_query(input_query) == expected


def test_get_highlight_snippets_preserves_individual_fragments():
    """_get_highlight_snippets returns cleaned highlight fragments without flattening them."""
    data = SolrResponse(
        highlighting={
            "doc-1": {
                "main_content": [
                    "First <em>kernel</em> snippet.",
                    "Second snippet about <em>panic</em>.",
                ]
            }
        }
    )

    snippets = _get_highlight_snippets(data, "doc-1", query="kernel panic")

    assert snippets == ["First kernel snippet.", "Second snippet about panic."]


def test_get_highlight_snippets_deduplicates_across_alias_keys():
    """_get_highlight_snippets deduplicates repeated fragments returned under multiple keys."""
    data = SolrResponse(
        highlighting={
            "doc-1": {"main_content": ["Repeated <em>snippet</em>."]},
            "/docs/doc-1": {"main_content": ["Repeated <em>snippet</em>.", "Unique snippet."]},
        }
    )

    snippets = _get_highlight_snippets(data, "doc-1", "/docs/doc-1", query="snippet")

    assert snippets == ["Repeated snippet.", "Unique snippet."]


# ---------------------------------------------------------------------------
# SolrResponse model validation
# ---------------------------------------------------------------------------


def test_solr_response_rejects_error_payload():
    """SolrResponse model validator raises on Solr error payloads."""
    with pytest.raises(ValidationError, match="Solr returned error"):
        SolrResponse.model_validate({"error": {"msg": "bad", "code": 400}})


def test_solr_response_defaults_on_missing_response():
    """Missing 'response' key produces an empty SolrResponse via defaults."""
    parsed = SolrResponse.model_validate({"unexpected": True})
    assert parsed.response.numFound == 0
    assert parsed.response.docs == []
