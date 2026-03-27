"""Shared fixtures for RAG subpackage tests."""

import httpx
import pytest


@pytest.fixture
def rag_chunk_response():
    """Realistic portal-rag chunk response shared by lexical, hybrid, and semantic tests."""
    return {
        "response": {
            "numFound": 1,
            "docs": [
                {
                    "doc_id": "/security/cve/CVE-2024-42225_chunk_2",
                    "parent_id": "/security/cve/CVE-2024-42225",
                    "title": "CVE-2024-42225 - Red Hat Customer Portal",
                    "chunk": "A potential flaw was found...",
                    "headings": "CVE-2024-42225,Description",
                    "chunk_index": 2,
                    "num_tokens": 49,
                    "is_chunk": True,
                }
            ],
        }
    }


@pytest.fixture
async def rag_client():
    """Async httpx client with automatic cleanup for RAG tests."""
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture(autouse=True)
async def _patch_expand_chunks(monkeypatch):
    """Patch expand_chunks to return input unchanged in all tool tests.

    The fused search path calls expand_chunks() after dedup on all paths.
    Existing tests only mock the hybrid endpoint, so unmocked expansion
    calls would hit real Solr (or raise in respx strict mode). This
    fixture makes expansion a no-op unless a test explicitly overrides it.
    """

    async def _passthrough(chunks, **kwargs):
        return chunks

    monkeypatch.setattr("okp_mcp.rag.tools.expand_chunks", _passthrough)
