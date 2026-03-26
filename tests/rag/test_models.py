"""Unit tests for RagDocument and RagResponse Pydantic models."""

import pytest

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


def test_rag_document_explicit_fields_all_populated():
    """RagDocument with all new explicit fields populated, assert each is accessible and has correct type."""
    doc = RagDocument(
        doc_id="test_id",
        parent_id="parent_id",
        title="Test Title",
        chunk="Test chunk content",
        rrf_score=0.95,
        headings="Heading1,Heading2",
        online_source_url="https://example.com/page",
        product=["RHEL 9", "RHEL 8"],
        product_version="9.0",
        chunk_index=2,
        num_tokens=49,
        source_path="/path/to/source",
        documentKind="article",
        score=0.87,
    )
    assert doc.headings == "Heading1,Heading2"
    assert doc.online_source_url == "https://example.com/page"
    assert doc.product == ["RHEL 9", "RHEL 8"]
    assert doc.product_version == "9.0"
    assert doc.chunk_index == 2
    assert doc.num_tokens == 49
    assert doc.source_path == "/path/to/source"
    assert doc.documentKind == "article"
    assert doc.score == 0.87


@pytest.mark.parametrize(
    "field_name",
    [
        "headings",
        "online_source_url",
        "product",
        "product_version",
        "chunk_index",
        "num_tokens",
        "source_path",
        "documentKind",
        "score",
    ],
)
def test_rag_document_explicit_field_defaults_to_none(field_name: str):
    """RagDocument() with no args has None for each explicit field."""
    doc = RagDocument()
    assert getattr(doc, field_name) is None


def test_rag_document_product_as_list_of_strings():
    """product field parsed as list[str]."""
    doc = RagDocument(product=["Red Hat Enterprise Linux", "RHEL 9"])
    assert doc.product == ["Red Hat Enterprise Linux", "RHEL 9"]
    assert isinstance(doc.product, list)
    assert all(isinstance(p, str) for p in doc.product)


def test_rag_document_extra_allow_alongside_explicit():
    """Construct with both explicit AND unknown extra fields; verify both accessible."""
    doc = RagDocument(
        doc_id="test",
        headings="Test Heading",
        is_chunk=True,
        custom_field="custom_value",
    )
    assert doc.doc_id == "test"
    assert doc.headings == "Test Heading"
    assert doc.is_chunk is True  # type: ignore[attr-defined]
    assert doc.custom_field == "custom_value"  # type: ignore[attr-defined]


def test_rag_document_realistic_solr_response_shape():
    """Construct from dict matching realistic Solr response."""
    solr_doc = {
        "doc_id": "/security/cve/CVE-2024-42225_chunk_2",
        "parent_id": "/security/cve/CVE-2024-42225",
        "title": "CVE-2024-42225 - Red Hat Customer Portal",
        "chunk": "A potential flaw was found...",
        "headings": "CVE-2024-42225,Description",
        "chunk_index": 2,
        "num_tokens": 49,
        "is_chunk": True,
    }
    doc = RagDocument(**solr_doc)
    assert doc.doc_id == "/security/cve/CVE-2024-42225_chunk_2"
    assert doc.parent_id == "/security/cve/CVE-2024-42225"
    assert doc.title == "CVE-2024-42225 - Red Hat Customer Portal"
    assert doc.chunk == "A potential flaw was found..."
    assert doc.headings == "CVE-2024-42225,Description"
    assert doc.chunk_index == 2
    assert doc.num_tokens == 49
    assert doc.is_chunk is True  # type: ignore[attr-defined]


def test_rag_document_product_rejects_plain_string():
    """product field rejects plain string (not a list), raises pydantic.ValidationError."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RagDocument(product="some string")
