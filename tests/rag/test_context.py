"""Tests for RAG context expansion utilities."""

from unittest.mock import AsyncMock

import httpx
import respx

from okp_mcp.rag.context import (
    DEFAULT_MAX_TOTAL_TOKENS,
    DEFAULT_WINDOW,
    PARENT_FL,
    expand_chunk,
    expand_chunks,
    fetch_full_document_chunks,
    fetch_parent_metadata,
    fetch_sibling_chunks,
    merge_chunks,
)
from okp_mcp.rag.models import RagDocument

SOLR_URL = "http://test-solr:8984"
SELECT_ENDPOINT = f"{SOLR_URL}/solr/portal-rag/select"


def _parent_response(total_chunks: int = 5, total_tokens: int = 800) -> dict:
    """Build a Solr response containing a single parent document record."""
    return {
        "response": {
            "numFound": 1,
            "docs": [{"doc_id": "/doc/parent", "total_chunks": total_chunks, "total_tokens": total_tokens}],
        }
    }


def _chunk_response(count: int, start_index: int = 0) -> dict:
    """Build a Solr response with sequential chunks from the same parent."""
    return {
        "response": {
            "numFound": count,
            "docs": [
                {
                    "doc_id": f"/doc/parent_chunk_{start_index + i}",
                    "parent_id": "/doc/parent",
                    "title": "Test Doc",
                    "chunk": f"Content of chunk {start_index + i}.",
                    "chunk_index": start_index + i,
                    "num_tokens": 100,
                }
                for i in range(count)
            ],
        }
    }


EMPTY_RESPONSE = {"response": {"numFound": 0, "docs": []}}


# --- fetch_parent_metadata ---


async def test_fetch_parent_metadata_returns_parent(rag_client):
    """fetch_parent_metadata returns a RagDocument with total_chunks and total_tokens."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=_parent_response(19, 2698)))
        result = await fetch_parent_metadata("/doc/parent", client=rag_client, solr_url=SOLR_URL)

    assert result is not None
    assert result.total_chunks == 19
    assert result.total_tokens == 2698
    params = route.calls[0].request.url.params
    assert params["fq"] == "is_chunk:false"
    assert params["fl"] == PARENT_FL


async def test_fetch_parent_metadata_returns_none_when_not_found(rag_client):
    """fetch_parent_metadata returns None when Solr finds no matching parent."""
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=EMPTY_RESPONSE))
        result = await fetch_parent_metadata("/nonexistent", client=rag_client, solr_url=SOLR_URL)

    assert result is None


# --- fetch_sibling_chunks ---


async def test_fetch_sibling_chunks_sends_range_query(rag_client):
    """fetch_sibling_chunks sends chunk_index range filter and sorts ascending."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SELECT_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_chunk_response(5, start_index=3))
        )
        result = await fetch_sibling_chunks(
            "/doc/parent",
            5,
            window=2,
            client=rag_client,
            solr_url=SOLR_URL,
        )

    assert len(result) == 5
    params = route.calls[0].request.url.params
    fq_values = params.multi_items()
    fq_params = [v for k, v in fq_values if k == "fq"]
    assert any("chunk_index:[3 TO 7]" in fq for fq in fq_params)
    assert params["sort"] == "chunk_index asc"


async def test_fetch_sibling_chunks_clamps_start_at_zero(rag_client):
    """fetch_sibling_chunks clamps the range start to 0 for low chunk indices."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=_chunk_response(2)))
        await fetch_sibling_chunks("/doc/parent", 0, window=2, client=rag_client, solr_url=SOLR_URL)

    fq_values = route.calls[0].request.url.params.multi_items()
    fq_params = [v for k, v in fq_values if k == "fq"]
    assert any("chunk_index:[0 TO 2]" in fq for fq in fq_params)


async def test_fetch_sibling_chunks_empty_result(rag_client):
    """fetch_sibling_chunks returns empty list when no chunks match."""
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=EMPTY_RESPONSE))
        result = await fetch_sibling_chunks("/doc/parent", 5, client=rag_client, solr_url=SOLR_URL)

    assert result == []


# --- fetch_full_document_chunks ---


async def test_fetch_full_document_chunks_fetches_all(rag_client):
    """fetch_full_document_chunks retrieves all chunks sorted by chunk_index."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=_chunk_response(10)))
        result = await fetch_full_document_chunks(
            "/doc/parent",
            total_chunks=10,
            client=rag_client,
            solr_url=SOLR_URL,
        )

    assert len(result) == 10
    params = route.calls[0].request.url.params
    assert params["rows"] == "10"
    assert params["sort"] == "chunk_index asc"


# --- merge_chunks ---


def test_merge_chunks_concatenates_texts():
    """merge_chunks joins chunk texts with blank-line separators."""
    anchor = RagDocument(
        doc_id="c_2",
        parent_id="p",
        title="My Doc",
        chunk="Middle.",
        chunk_index=2,
        num_tokens=50,
        online_source_url="https://example.com",
    )
    chunks = [
        RagDocument(doc_id="c_1", chunk="Before.", num_tokens=40),
        RagDocument(doc_id="c_2", chunk="Middle.", num_tokens=50),
        RagDocument(doc_id="c_3", chunk="After.", num_tokens=60),
    ]
    merged = merge_chunks(anchor, chunks)
    assert merged.chunk == "Before.\n\nMiddle.\n\nAfter."
    assert merged.num_tokens == 150
    # Metadata preserved from anchor
    assert merged.title == "My Doc"
    assert merged.online_source_url == "https://example.com"
    assert merged.doc_id == "c_2"


def test_merge_chunks_single_chunk_returns_anchor():
    """merge_chunks with one chunk returns the anchor unchanged."""
    anchor = RagDocument(doc_id="only", chunk="Content.", num_tokens=50)
    result = merge_chunks(anchor, [anchor])
    assert result is anchor


def test_merge_chunks_skips_none_chunks():
    """merge_chunks skips chunks with None chunk text."""
    anchor = RagDocument(doc_id="a", chunk="Real content.", num_tokens=50)
    chunks = [
        RagDocument(doc_id="empty", chunk=None, num_tokens=0),
        RagDocument(doc_id="a", chunk="Real content.", num_tokens=50),
    ]
    merged = merge_chunks(anchor, chunks)
    assert merged.chunk == "Real content."
    assert merged.num_tokens == 50


def test_merge_chunks_empty_list_returns_anchor():
    """merge_chunks with empty chunk list returns the anchor unchanged."""
    anchor = RagDocument(doc_id="a", chunk="Content.", num_tokens=50)
    result = merge_chunks(anchor, [])
    assert result is anchor


# --- expand_chunk ---


async def test_expand_chunk_small_doc_fetches_full(rag_client):
    """expand_chunk retrieves all chunks when parent total_tokens is within threshold."""
    anchor = RagDocument(
        doc_id="/doc/parent_chunk_2",
        parent_id="/doc/parent",
        title="Small Doc",
        chunk="Matched content.",
        chunk_index=2,
        num_tokens=100,
    )
    with respx.mock(assert_all_called=True) as router:
        # First call: parent metadata (small doc)
        router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=_parent_response(total_chunks=5, total_tokens=500)),
                httpx.Response(200, json=_chunk_response(5)),
            ]
        )
        result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)

    assert result.num_tokens == 500  # 5 chunks x 100 tokens
    assert result.chunk is not None
    assert "Content of chunk 0." in result.chunk
    assert "Content of chunk 4." in result.chunk
    assert result.title == "Small Doc"  # anchor metadata preserved


async def test_expand_chunk_large_doc_uses_window(rag_client):
    """expand_chunk uses windowed retrieval when parent exceeds token threshold."""
    anchor = RagDocument(
        doc_id="/doc/parent_chunk_10",
        parent_id="/doc/parent",
        title="Large Doc",
        chunk="Matched.",
        chunk_index=10,
        num_tokens=100,
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=_parent_response(total_chunks=200, total_tokens=50000)),
                httpx.Response(200, json=_chunk_response(5, start_index=8)),
            ]
        )
        result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL, window=2)

    assert result.num_tokens == 500  # 5 chunks x 100
    sibling_call = route.calls[1].request.url.params
    fq_values = sibling_call.multi_items()
    fq_params = [v for k, v in fq_values if k == "fq"]
    assert any("chunk_index:[8 TO 12]" in fq for fq in fq_params)


async def test_expand_chunk_no_parent_id_returns_unchanged(rag_client):
    """expand_chunk returns the original chunk when parent_id is None."""
    anchor = RagDocument(doc_id="orphan", chunk="Content.", chunk_index=0, num_tokens=50)
    result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)
    assert result is anchor


async def test_expand_chunk_no_chunk_index_returns_unchanged(rag_client):
    """expand_chunk returns the original chunk when chunk_index is None."""
    anchor = RagDocument(doc_id="no_idx", parent_id="/doc/parent", chunk="Content.", num_tokens=50)
    result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)
    assert result is anchor


async def test_expand_chunk_parent_not_found_returns_unchanged(rag_client):
    """expand_chunk returns the original chunk when parent metadata lookup returns nothing."""
    anchor = RagDocument(
        doc_id="c_2",
        parent_id="/doc/missing",
        chunk="Content.",
        chunk_index=2,
        num_tokens=50,
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=EMPTY_RESPONSE))
        result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)

    assert result is anchor


async def test_expand_chunk_parent_metadata_timeout_returns_unchanged():
    """expand_chunk returns the original chunk when parent metadata lookup times out."""
    anchor = RagDocument(
        doc_id="c_2",
        parent_id="/doc/parent",
        chunk="Content.",
        chunk_index=2,
        num_tokens=50,
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))
    result = await expand_chunk(anchor, client=client, solr_url=SOLR_URL)
    assert result is anchor


async def test_expand_chunk_sibling_fetch_error_returns_unchanged(rag_client):
    """expand_chunk returns the original chunk when sibling chunk retrieval fails."""
    anchor = RagDocument(
        doc_id="c_5",
        parent_id="/doc/parent",
        chunk="Content.",
        chunk_index=5,
        num_tokens=50,
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=_parent_response(total_chunks=100, total_tokens=50000)),
                httpx.Response(500, text="Internal Server Error"),
            ]
        )
        result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)

    assert result is anchor


async def test_expand_chunk_custom_thresholds(rag_client):
    """expand_chunk respects custom max_total_tokens and window parameters."""
    anchor = RagDocument(
        doc_id="c_3",
        parent_id="/doc/parent",
        chunk="Content.",
        chunk_index=3,
        num_tokens=50,
    )
    # 2000 tokens is under the custom threshold of 3000, so full retrieval
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=_parent_response(total_chunks=10, total_tokens=2000)),
                httpx.Response(200, json=_chunk_response(10)),
            ]
        )
        result = await expand_chunk(
            anchor,
            client=rag_client,
            solr_url=SOLR_URL,
            max_total_tokens=3000,
            window=1,
        )

    assert result.num_tokens == 1000  # 10 chunks x 100


async def test_expand_chunk_parent_missing_total_tokens_uses_window(rag_client):
    """expand_chunk uses windowed retrieval when parent has None total_tokens."""
    anchor = RagDocument(
        doc_id="c_3",
        parent_id="/doc/parent",
        chunk="Content.",
        chunk_index=3,
        num_tokens=50,
    )
    parent_resp = {"response": {"numFound": 1, "docs": [{"doc_id": "/doc/parent"}]}}
    with respx.mock(assert_all_called=True) as router:
        router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=parent_resp),
                httpx.Response(200, json=_chunk_response(5, start_index=1)),
            ]
        )
        result = await expand_chunk(anchor, client=rag_client, solr_url=SOLR_URL)

    assert result.num_tokens == 500


# --- expand_chunks ---


async def test_expand_chunks_parallel_execution(rag_client):
    """expand_chunks expands multiple chunks concurrently."""
    chunks = [
        RagDocument(doc_id=f"c_{i}", parent_id=f"/parent_{i}", chunk=f"Chunk {i}.", chunk_index=0, num_tokens=50)
        for i in range(3)
    ]
    with respx.mock() as router:
        # Each chunk triggers: parent metadata + full doc fetch
        router.get(SELECT_ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=_parent_response(total_chunks=2, total_tokens=200)),
                httpx.Response(200, json=_chunk_response(2)),
            ]
            * 3  # 3 chunks, each gets 2 responses
        )
        result = await expand_chunks(chunks, client=rag_client, solr_url=SOLR_URL)

    assert len(result) == 3
    # All should be expanded (merged from 2 chunks each)
    for doc in result:
        assert doc.num_tokens == 200


async def test_expand_chunks_empty_input():
    """expand_chunks returns empty list for empty input."""
    client = AsyncMock(spec=httpx.AsyncClient)
    result = await expand_chunks([], client=client, solr_url=SOLR_URL)
    assert result == []


async def test_expand_chunks_preserves_order(rag_client):
    """expand_chunks preserves the input order of chunks."""
    chunks = [
        RagDocument(doc_id=f"c_{i}", parent_id=f"/parent_{i}", chunk=f"Chunk {i}.", chunk_index=0, num_tokens=50)
        for i in range(3)
    ]
    with respx.mock() as router:
        router.get(SELECT_ENDPOINT).mock(return_value=httpx.Response(200, json=EMPTY_RESPONSE))
        result = await expand_chunks(chunks, client=rag_client, solr_url=SOLR_URL)

    # All chunks returned unchanged (parent not found), but in order
    assert [d.doc_id for d in result] == ["c_0", "c_1", "c_2"]


# --- Default constants ---


def test_default_constants():
    """Module constants have expected values."""
    assert DEFAULT_WINDOW == 2
    assert DEFAULT_MAX_TOTAL_TOKENS == 4000
    assert "total_chunks" in PARENT_FL
    assert "total_tokens" in PARENT_FL
