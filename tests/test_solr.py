"""Tests for SOLR query client lifecycle and query cleaning behavior."""

# pyright: reportMissingImports=false

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import respx

from okp_mcp.config import ServerConfig
from okp_mcp.solr import _clean_query, _get_highlight_snippets, _solr_query

_SOLR_ENDPOINT = ServerConfig().solr_endpoint


async def test_solr_query_uses_provided_shared_client(sample_solr_response):
    """Provided client is used directly instead of constructing a new one."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        shared_client = httpx.AsyncClient(timeout=30.0)
        try:
            with patch(
                "okp_mcp.solr.httpx.AsyncClient", side_effect=AssertionError("constructor should not be called")
            ):
                data = await _solr_query({"q": "kernel panic"}, client=shared_client, solr_endpoint=_SOLR_ENDPOINT)
        finally:
            await shared_client.aclose()

    assert route.called
    assert data == sample_solr_response


async def test_solr_query_does_not_close_shared_client():
    """Provided shared client is not closed by _solr_query."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": {"numFound": 0, "docs": []}, "highlighting": {}}

    shared_client = AsyncMock(spec=httpx.AsyncClient)
    shared_client.get = AsyncMock(return_value=response)
    shared_client.aclose = AsyncMock()

    await _solr_query({"q": "rpm-ostree"}, client=shared_client, solr_endpoint=_SOLR_ENDPOINT)

    shared_client.get.assert_awaited_once()
    shared_client.aclose.assert_not_awaited()


async def test_solr_query_creates_and_closes_client_when_not_provided(sample_solr_response):
    """When no client is provided, _solr_query creates and closes one."""
    created_client = httpx.AsyncClient(timeout=30.0)
    original_aclose = created_client.aclose
    closed = False

    async def _tracking_aclose() -> None:
        nonlocal closed
        closed = True
        await original_aclose()

    created_client.aclose = _tracking_aclose

    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client) as client_ctor,
        respx.mock(assert_all_called=True) as router,
    ):
        route = router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=sample_solr_response))
        data = await _solr_query({"q": "openshift"}, solr_endpoint=_SOLR_ENDPOINT)

    client_ctor.assert_called_once_with(timeout=30.0)
    assert route.called
    assert closed
    assert data == sample_solr_response


@pytest.mark.parametrize("use_shared_client", [True, False])
async def test_solr_query_timeout_exception_propagates(use_shared_client):
    """TimeoutException is re-raised for shared and owned client paths."""
    timeout_error = httpx.TimeoutException("slow SOLR")

    if use_shared_client:
        shared_client = AsyncMock(spec=httpx.AsyncClient)
        shared_client.get = AsyncMock(side_effect=timeout_error)
        with pytest.raises(httpx.TimeoutException):
            await _solr_query({"q": "timeout"}, client=shared_client, solr_endpoint=_SOLR_ENDPOINT)
        shared_client.aclose.assert_not_awaited()
        return

    created_client = AsyncMock(spec=httpx.AsyncClient)
    created_client.get = AsyncMock(side_effect=timeout_error)
    created_client.aclose = AsyncMock()
    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client),
        pytest.raises(httpx.TimeoutException),
    ):
        await _solr_query({"q": "timeout"}, solr_endpoint=_SOLR_ENDPOINT)
    created_client.aclose.assert_awaited_once()


@pytest.mark.parametrize("use_shared_client", [True, False])
async def test_solr_query_http_status_error_propagates(use_shared_client):
    """HTTPStatusError is re-raised for shared and owned client paths."""
    request = httpx.Request("GET", _SOLR_ENDPOINT)
    response = httpx.Response(503, request=request, text="service unavailable")
    status_error = httpx.HTTPStatusError("bad status", request=request, response=response)

    if use_shared_client:
        shared_client = AsyncMock(spec=httpx.AsyncClient)
        shared_client.get = AsyncMock(side_effect=status_error)
        with pytest.raises(httpx.HTTPStatusError):
            await _solr_query({"q": "status"}, client=shared_client, solr_endpoint=_SOLR_ENDPOINT)
        shared_client.aclose.assert_not_awaited()
        return

    created_client = AsyncMock(spec=httpx.AsyncClient)
    created_client.get = AsyncMock(side_effect=status_error)
    created_client.aclose = AsyncMock()
    with (
        patch("okp_mcp.solr.httpx.AsyncClient", return_value=created_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await _solr_query({"q": "status"}, solr_endpoint=_SOLR_ENDPOINT)
    created_client.aclose.assert_awaited_once()


async def test_solr_query_respx_regression_guard(solr_mock, sample_solr_response):
    """Existing respx endpoint mocking keeps working after client refactor."""
    data = await _solr_query({"q": "selinux"}, solr_endpoint=_SOLR_ENDPOINT)

    assert solr_mock.called
    assert data["response"]["numFound"] == sample_solr_response["response"]["numFound"]


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
        ("Can I run a RHEL 6 container on RHEL 9?", "RHEL 6 container RHEL 9"),
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
    data = {
        "highlighting": {
            "doc-1": {
                "main_content": [
                    "First <em>kernel</em> snippet.",
                    "Second snippet about <em>panic</em>.",
                ]
            }
        }
    }

    snippets = _get_highlight_snippets(data, "doc-1", query="kernel panic")

    assert snippets == ["First kernel snippet.", "Second snippet about panic."]


def test_get_highlight_snippets_deduplicates_across_alias_keys():
    """_get_highlight_snippets deduplicates repeated fragments returned under multiple keys."""
    data = {
        "highlighting": {
            "doc-1": {"main_content": ["Repeated <em>snippet</em>."]},
            "/docs/doc-1": {"main_content": ["Repeated <em>snippet</em>.", "Unique snippet."]},
        }
    }

    snippets = _get_highlight_snippets(data, "doc-1", "/docs/doc-1", query="snippet")

    assert snippets == ["Repeated snippet.", "Unique snippet."]
