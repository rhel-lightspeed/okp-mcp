"""Functional tests for document retrieval quality against live Solr.

Tests call ``_run_portal_search()`` directly against a running Solr instance
(default: localhost:8983) and assert on document identity, position, and chunk
content.  No LLM is involved; assertions are fully deterministic.

Run with::

    uv run pytest -m functional -v

Requires: OKP Solr container running (``podman-compose up -d``)
"""

import httpx
import pytest
from functional_cases import FUNCTIONAL_TEST_CASES, FunctionalCase

from okp_mcp.config import ServerConfig
from okp_mcp.portal import PortalChunk, _run_portal_search

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_identifiers(chunk: PortalChunk) -> str:
    """Concatenate identifying fields of a single chunk for substring matching."""
    return " ".join(filter(None, [chunk.doc_id, chunk.parent_id, chunk.title, chunk.online_source_url])).lower()


def _all_identifiers(chunks: list[PortalChunk]) -> str:
    """Concatenate all identifying fields across all chunks."""
    return " ".join(_chunk_identifiers(c) for c in chunks)


def _all_chunk_text(chunks: list[PortalChunk]) -> str:
    """Concatenate all chunk text (lowercased) for content assertions."""
    return " ".join(c.chunk for c in chunks).lower()


def _find_doc_position(chunks: list[PortalChunk], ref: str) -> int | None:
    """Find 0-indexed position of first chunk matching a doc ref substring."""
    ref_lower = ref.lower()
    for i, chunk in enumerate(chunks):
        if ref_lower in _chunk_identifiers(chunk):
            return i
    return None


def _parent_ids(chunks: list[PortalChunk]) -> list[str]:
    """Extract parent_id list for error messages."""
    return [c.parent_id or "?" for c in chunks]


async def _portal_search(question: str, max_results: int = 7) -> tuple[list[PortalChunk], bool]:
    """Run a portal search against the live Solr instance, skipping if unreachable."""
    config = ServerConfig()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            return await _run_portal_search(
                question, client=client, solr_endpoint=config.solr_endpoint, max_results=max_results
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            pytest.skip(f"Solr not reachable at {config.solr_url}")
            raise  # unreachable, satisfies type checker


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.functional
@pytest.mark.parametrize("case", FUNCTIONAL_TEST_CASES)
async def test_search_retrieval(case: FunctionalCase) -> None:
    """Verify a single portal search returns the right documents with key content.

    Asserts on four dimensions of retrieval quality:

    1. Document presence: at least one expected doc appears in results
    2. Position: expected doc appears within top N (if specified)
    3. Content: chunk text contains required facts
    4. Result count: not more than max_result_count (if specified)
    """
    chunks, _ = await _portal_search(case.question)

    assert chunks, f"No results for: {case.question}"

    # 1. At least one expected doc must match
    all_ids = _all_identifiers(chunks)
    matched = [ref for ref in case.expected_docs if ref.lower() in all_ids]
    assert matched, f"No expected docs found.\n  Expected (any of): {case.expected_docs}\n  Got: {_parent_ids(chunks)}"

    # 2. Position check
    if case.max_position is not None:
        positions = [_find_doc_position(chunks, ref) for ref in case.expected_docs]
        best = min((p for p in positions if p is not None), default=None)
        assert best is not None and best < case.max_position, (
            f"Expected doc not in top {case.max_position}.\n"
            f"  Best position: {best}\n"
            f"  Top results: {_parent_ids(chunks[: case.max_position])}"
        )

    # 3. Content assertions
    content = _all_chunk_text(chunks)
    for fact in case.expected_content:
        if isinstance(fact, tuple):
            assert any(f.lower() in content for f in fact), f"None of alternatives found in chunk text: {fact}"
        else:
            assert fact.lower() in content, f"Expected content not found: '{fact}'"

    # 4. Result count
    if case.max_result_count is not None:
        assert len(chunks) <= case.max_result_count, f"Too many results: {len(chunks)} > {case.max_result_count}"
