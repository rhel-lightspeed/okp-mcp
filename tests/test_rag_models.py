"""Unit tests for RagDocument and RagResponse Pydantic models."""

from okp_mcp.rag.models import RagDocument, RagResponse


def test_rag_document_default_construction():
    """RagDocument() with no args has all named fields as None."""
    doc = RagDocument()
    assert doc.doc_id is None
    assert doc.parent_id is None
    assert doc.title is None
    assert doc.chunk is None
    assert doc.rrf_score is None


def test_rag_document_extra_field_storage():
    """RagDocument stores extra fields via extra='allow' and makes them accessible."""
    doc = RagDocument(doc_id="x", unknown_field="y")
    assert doc.doc_id == "x"
    assert doc.unknown_field == "y"  # type: ignore[attr-defined]


def test_rag_document_equality_with_extra_fields():
    """RagDocument equality includes extra fields."""
    doc1 = RagDocument(doc_id="a", extra_f="v")
    doc2 = RagDocument(doc_id="a", extra_f="v")
    assert doc1 == doc2


def test_rag_document_model_copy_basic():
    """RagDocument.model_copy() preserves original fields and applies updates."""
    original = RagDocument(doc_id="orig")
    copied = original.model_copy(update={"rrf_score": 0.5})
    assert copied.doc_id == "orig"
    assert copied.rrf_score == 0.5
    assert original.rrf_score is None


def test_rag_document_model_copy_preserves_extra_fields():
    """RagDocument.model_copy() preserves extra fields when updating."""
    original = RagDocument(doc_id="orig", extra_k="extra_v")
    copied = original.model_copy(update={"rrf_score": 0.5})
    assert copied.doc_id == "orig"
    assert copied.rrf_score == 0.5
    assert copied.extra_k == "extra_v"  # type: ignore[attr-defined]


def test_rag_response_empty():
    """RagResponse with empty docs list works and attributes are accessible."""
    response = RagResponse(num_found=0, docs=[])
    assert response.num_found == 0
    assert response.docs == []


def test_rag_response_with_documents():
    """RagResponse with multiple documents stores and retrieves them correctly."""
    doc_a = RagDocument(doc_id="a")
    doc_b = RagDocument(doc_id="b")
    response = RagResponse(num_found=2, docs=[doc_a, doc_b])
    assert response.num_found == 2
    assert len(response.docs) == 2
    assert response.docs[0].doc_id == "a"
    assert response.docs[1].doc_id == "b"


def test_rag_document_getattr_extra_field():
    """getattr() retrieves extra fields, supporting RRF _accumulate_scores pattern."""
    doc = RagDocument(doc_id="test", some_extra_field="extra_value")
    assert getattr(doc, "some_extra_field", None) == "extra_value"
    assert getattr(doc, "nonexistent_field", None) is None
