"""Query functions for the portal-rag Solr core."""

from .hybrid import hybrid_search
from .lexical import lexical_search
from .models import RagDocument, RagResponse
from .rrf import reciprocal_rank_fusion
from .semantic import semantic_search, semantic_text_search

__all__ = [
    "RagDocument",
    "RagResponse",
    "hybrid_search",
    "lexical_search",
    "reciprocal_rank_fusion",
    "semantic_search",
    "semantic_text_search",
]
