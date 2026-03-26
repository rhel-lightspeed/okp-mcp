"""Tests for RAG result deduplication and formatting utilities."""

import pytest

from okp_mcp.rag.formatting import deduplicate_chunks, format_rag_result
from okp_mcp.rag.models import RagDocument


def test_deduplicate_chunks_basic_two_parents():
    """deduplicate_chunks keeps one chunk per parent_id."""
    docs = [
        RagDocument(doc_id="a_1", parent_id="a", chunk="chunk1", num_tokens=50),
        RagDocument(doc_id="a_2", parent_id="a", chunk="chunk2", num_tokens=60),
        RagDocument(doc_id="b_1", parent_id="b", chunk="chunk3", num_tokens=45),
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 2
    assert result[0].doc_id == "a_1"  # first from parent "a"
    assert result[1].doc_id == "b_1"  # first from parent "b"


def test_deduplicate_chunks_filters_short_chunks():
    """deduplicate_chunks drops chunks below min_tokens threshold."""
    docs = [
        RagDocument(doc_id="short", parent_id="a", chunk="hi", num_tokens=10),
        RagDocument(doc_id="long", parent_id="a", chunk="detailed content", num_tokens=100),
    ]
    result = deduplicate_chunks(docs, min_tokens=30)
    assert len(result) == 1
    assert result[0].doc_id == "long"


def test_deduplicate_chunks_prefers_earlier_input_position():
    """deduplicate_chunks selects the chunk that appears first in the input list."""
    docs = [
        RagDocument(doc_id="idx1", parent_id="a", chunk="content", num_tokens=50, chunk_index=1),
        RagDocument(doc_id="idx0", parent_id="a", chunk="title only", num_tokens=50, chunk_index=0),
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 1
    assert result[0].doc_id == "idx1"


def test_deduplicate_chunks_none_parent_treated_as_unique():
    """deduplicate_chunks keeps all chunks with None parent_id."""
    docs = [
        RagDocument(doc_id="no_parent_1", parent_id=None, chunk="orphan1", num_tokens=50),
        RagDocument(doc_id="no_parent_2", parent_id=None, chunk="orphan2", num_tokens=60),
        RagDocument(doc_id="has_parent", parent_id="a", chunk="child", num_tokens=70),
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 3  # all kept


def test_deduplicate_chunks_none_num_tokens_not_filtered():
    """deduplicate_chunks does not filter chunks with None num_tokens."""
    docs = [
        RagDocument(doc_id="unknown_tokens", parent_id="a", chunk="content", num_tokens=None),
    ]
    result = deduplicate_chunks(docs, min_tokens=30)
    assert len(result) == 1
    assert result[0].doc_id == "unknown_tokens"


def test_deduplicate_chunks_all_short_keeps_best():
    """deduplicate_chunks keeps at least one chunk per parent even if all are short."""
    docs = [
        RagDocument(doc_id="s1", parent_id="a", chunk="short1", num_tokens=10),
        RagDocument(doc_id="s2", parent_id="a", chunk="short2", num_tokens=15),
    ]
    result = deduplicate_chunks(docs, min_tokens=30)
    assert len(result) == 1
    assert result[0].doc_id == "s1"  # first in rank order


def test_deduplicate_chunks_empty_input():
    """deduplicate_chunks returns empty list for empty input."""
    assert deduplicate_chunks([]) == []


def test_deduplicate_chunks_single_chunk():
    """deduplicate_chunks with single chunk returns it unchanged."""
    doc = RagDocument(doc_id="only", parent_id="a", chunk="content", num_tokens=50)
    result = deduplicate_chunks([doc])
    assert len(result) == 1
    assert result[0].doc_id == "only"


def test_deduplicate_chunks_all_same_parent():
    """deduplicate_chunks with 5 chunks from 1 parent returns 1 result."""
    docs = [RagDocument(doc_id=f"c{i}", parent_id="same", chunk=f"chunk{i}", num_tokens=50 + i) for i in range(5)]
    result = deduplicate_chunks(docs)
    assert len(result) == 1
    assert result[0].doc_id == "c0"  # first in rank order


def test_deduplicate_chunks_preserves_rank_order():
    """deduplicate_chunks returns results in original rank order."""
    docs = [
        RagDocument(doc_id="b_1", parent_id="b", chunk="b_content", num_tokens=50),
        RagDocument(doc_id="a_1", parent_id="a", chunk="a_content", num_tokens=50),
        RagDocument(doc_id="c_1", parent_id="c", chunk="c_content", num_tokens=50),
        RagDocument(doc_id="a_2", parent_id="a", chunk="a_content2", num_tokens=60),
    ]
    result = deduplicate_chunks(docs)
    assert len(result) == 3
    assert [d.doc_id for d in result] == ["b_1", "a_1", "c_1"]


def test_format_rag_result_full():
    """format_rag_result with all fields produces complete markdown block."""
    doc = RagDocument(
        title="RHEL 9 Security Guide - Configuring SELinux",
        headings="SELinux,Configuring SELinux,Changing SELinux Modes",
        product=["Red Hat Enterprise Linux"],
        product_version="9",
        online_source_url="https://docs.redhat.com/en/documentation/configuring-selinux",
        chunk="To change the SELinux mode to enforcing: Edit the /etc/selinux/config file.",
    )
    result = format_rag_result(doc)
    assert "**RHEL 9 Security Guide - Configuring SELinux**" in result
    assert "Section: SELinux > Configuring SELinux > Changing SELinux Modes" in result
    assert "Product: Red Hat Enterprise Linux 9" in result
    assert "URL: https://docs.redhat.com/en/documentation/configuring-selinux" in result
    assert "To change the SELinux mode to enforcing" in result


def test_format_rag_result_minimal():
    """format_rag_result with only title and chunk omits optional lines."""
    doc = RagDocument(title="Test Page", chunk="Some content here.")
    result = format_rag_result(doc)
    assert "**Test Page**" in result
    assert "Some content here." in result
    assert "Section:" not in result
    assert "Product:" not in result
    assert "URL:" not in result


def test_format_rag_result_headings_parsing():
    """format_rag_result converts comma-separated headings to ' > ' separated."""
    doc = RagDocument(title="CVE Page", headings="CVE-2024-42225,Description,Impact")
    result = format_rag_result(doc)
    assert "Section: CVE-2024-42225 > Description > Impact" in result


@pytest.mark.parametrize(
    ("field_kwargs", "absent_label"),
    [
        ({"headings": None}, "Section:"),
        ({"product": None}, "Product:"),
        ({"online_source_url": None}, "URL:"),
    ],
    ids=["no-headings", "no-product", "no-url"],
)
def test_format_rag_result_omits_line_for_none_field(field_kwargs, absent_label):
    """format_rag_result omits the corresponding line when an optional field is None."""
    doc = RagDocument(title="Page", **field_kwargs)
    result = format_rag_result(doc)
    assert absent_label not in result


def test_format_rag_result_product_display():
    """format_rag_result displays single product correctly."""
    doc = RagDocument(title="Page", product=["Red Hat Enterprise Linux"])
    result = format_rag_result(doc)
    assert "Product: Red Hat Enterprise Linux" in result


def test_format_rag_result_multi_product():
    """format_rag_result joins multiple products with comma."""
    doc = RagDocument(title="Page", product=["RHEL", "OpenShift"])
    result = format_rag_result(doc)
    assert "Product: RHEL, OpenShift" in result


def test_format_rag_result_none_chunk():
    """format_rag_result omits content body when chunk is None."""
    doc = RagDocument(title="Page", chunk=None)
    result = format_rag_result(doc)
    # Should just have the title, no trailing blank line + content
    assert result.strip() == "**Page**"


def test_format_rag_result_product_with_version():
    """format_rag_result appends version after product on same line."""
    doc = RagDocument(title="Page", product=["Red Hat Enterprise Linux"], product_version="9.4")
    result = format_rag_result(doc)
    assert "Product: Red Hat Enterprise Linux 9.4" in result


def test_format_rag_result_no_title():
    """format_rag_result uses 'Untitled' when title is None."""
    doc = RagDocument(title=None, chunk="Some content.")
    result = format_rag_result(doc)
    assert "**Untitled**" in result
