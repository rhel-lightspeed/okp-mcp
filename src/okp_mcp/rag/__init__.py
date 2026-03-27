"""Query functions for the portal-rag Solr core and portal core fallback searches."""

from .common import RAG_FL, clean_rag_query
from .context import expand_chunk, expand_chunks, fetch_sibling_chunks, merge_chunks
from .formatting import deduplicate_chunks, format_rag_result
from .hybrid import hybrid_search
from .lexical import lexical_search
from .models import PortalDocument, PortalResponse, RagDocument, RagResponse
from .portal import PORTAL_FL, PortalDocumentKind, portal_search
from .rrf import reciprocal_rank_fusion
from .semantic import semantic_search, semantic_text_search

__all__ = [
    "PORTAL_FL",
    "PortalDocumentKind",
    "PortalDocument",
    "PortalResponse",
    "RAG_FL",
    "RagDocument",
    "RagResponse",
    "clean_rag_query",
    "deduplicate_chunks",
    "expand_chunk",
    "expand_chunks",
    "fetch_sibling_chunks",
    "format_rag_result",
    "hybrid_search",
    "lexical_search",
    "merge_chunks",
    "portal_search",
    "reciprocal_rank_fusion",
    "semantic_search",
    "semantic_text_search",
]
