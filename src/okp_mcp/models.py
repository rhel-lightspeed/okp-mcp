"""Pydantic models for OKP MCP server."""

from pydantic import BaseModel


class Chunk(BaseModel):
    """Represents a text chunk from a document."""

    chunk_index: int
    chunk: str


class Document(BaseModel):
    """Represents a document with metadata and search results."""

    doc_id: str
    title: str
    reference_url: str
    text: str
    score: float
    original_score: float | None = None
    matched_chunk_index: int | None = None
    chunks: list[Chunk] | None = None


class SearchResult(BaseModel):
    """Represents search results for one or more queries."""

    question: str | list[str]
    docs: list[Document]


class SampleDocument(BaseModel):
    """Sample document metadata for collection exploration."""

    doc_id: str
    title: str
    reference_url: str | None = None
    total_chunks: int | None = None
    total_tokens: int | None = None


class CollectionInfo(BaseModel):
    """Collection statistics and metadata for exploration."""

    total_documents: int
    parent_documents: int
    chunks: int
    document_kinds: dict[str, int]
    sample_documents: list[SampleDocument]
