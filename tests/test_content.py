"""Tests for okp_mcp.content module."""

import pytest

from okp_mcp.content import _select_within_budget
from okp_mcp.content import clean_content
from okp_mcp.content import clean_heading
from okp_mcp.content import doc_uri
from okp_mcp.content import format_sections
from okp_mcp.content import strip_boilerplate
from okp_mcp.content import truncate_content
from okp_mcp.outline import Section
from okp_mcp.types import SolrDoc


@pytest.mark.parametrize(
    "text,max_chars",
    [
        ("This is a short text.", 100),
        ("Exact.", 6),
    ],
    ids=["under-limit", "exact-limit"],
)
def test_truncate_content_no_op(text, max_chars):
    """Text at or under max_chars is returned unchanged."""
    assert truncate_content(text, max_chars=max_chars) == text


def test_truncate_content_over_limit():
    """Truncated text includes character counts in the truncation message."""
    result = truncate_content("x" * 100, max_chars=50)
    assert result.startswith("x" * 50)
    assert "[Content truncated - showing 50 of 100 characters]" in result


@pytest.mark.parametrize(
    "text,forbidden",
    [
        (
            "Main content.\n\nThis solution is part of Red Hat's fast-track publication program. Extra.",
            "fast-track publication program",
        ),
        (
            "Before. This content is not included. After.",
            "This content is not included.",
        ),
        (
            "A. This content is not included. B. This content is not included. C.",
            "This content is not included.",
        ),
    ],
    ids=["fast-track-footer", "not-included-marker", "multiple-occurrences"],
)
def test_strip_boilerplate_removes_patterns(text, forbidden):
    """Known boilerplate patterns are stripped from text."""
    assert forbidden not in strip_boilerplate(text)


def test_strip_boilerplate_preserves_clean_text():
    """Text without boilerplate passes through unchanged."""
    text = "Clean content with no boilerplate markers."
    assert strip_boilerplate(text) == text


@pytest.mark.parametrize(
    "text,max_chars,expected",
    [
        (None, 100, ""),
        ("", 100, ""),
        ("Normal text without boilerplate.", 1000, "Normal text without boilerplate."),
    ],
    ids=["none-input", "empty-string", "clean-passthrough"],
)
def test_clean_content_edge_cases(text, max_chars, expected):
    """Edge cases: None, empty string, and clean text pass through correctly."""
    assert clean_content(text, max_chars=max_chars) == expected


@pytest.mark.parametrize(
    "doc,expected",
    [
        (SolrDoc(id="/solutions/3257611/index.html"), "/solutions/3257611"),
        (SolrDoc(id="/articles/2585/index.html"), "/articles/2585"),
        (
            SolrDoc(id="/documentation/en-us/rhel/9/html-single/guide/index.html"),
            "/documentation/en-us/rhel/9/html-single/guide",
        ),
        (SolrDoc(view_uri="/security/cve/CVE-2024-9823/"), "/security/cve/CVE-2024-9823/"),
        (SolrDoc(view_uri="/errata/RHSA-2022:4915/"), "/errata/RHSA-2022:4915/"),
        (SolrDoc(), ""),
        (SolrDoc(id="/solutions/123"), "/solutions/123"),
        (SolrDoc(view_uri="/solutions/7134031", id="/solutions/7134031/index.html"), "/solutions/7134031"),
    ],
    ids=[
        "solution-id-strips-suffix",
        "article-id-strips-suffix",
        "documentation-id-strips-suffix",
        "cve-view-uri-unchanged",
        "errata-view-uri-unchanged",
        "empty-doc-returns-empty",
        "no-suffix-unchanged",
        "view-uri-preferred-over-id",
    ],
)
def test_doc_uri(doc, expected):
    """doc_uri returns canonical URL path, preferring view_uri and stripping /index.html."""
    assert doc_uri(doc) == expected


def test_clean_content_strips_then_truncates():
    """Both boilerplate patterns are stripped before truncation is applied."""
    text = (
        "Useful. " * 50
        + "This content is not included. "
        + "More useful. " * 50
        + "This solution is part of Red Hat's fast-track publication program."
    )
    result = clean_content(text, max_chars=300)
    assert "This content is not included." not in result
    assert "fast-track publication program" not in result


# --- _select_within_budget tests ---


def test_select_within_budget_all_fit():
    """Results fitting within budget are all included without a truncation notice."""
    results = ["short1", "short2", "short3"]
    output = _select_within_budget(results, max_chars=1000, query="test")
    assert "short1" in output
    assert "short2" in output
    assert "short3" in output
    assert "Budget reached" not in output


def test_select_within_budget_drops_tail():
    """Results exceeding the budget are dropped with a count message."""
    big = "x" * 2000
    results = [big] * 5
    output = _select_within_budget(results, max_chars=5000, query="test")
    assert "Budget reached" in output
    assert "of 5 results" in output


def test_select_within_budget_single_huge_truncated():
    """A single result exceeding the budget is hard-truncated via truncate_content."""
    huge = "y" * 50_000
    output = _select_within_budget([huge], max_chars=1000, query="test")
    assert len(output) <= 1200  # slack for the truncation message itself
    assert "Content truncated" in output


def test_select_within_budget_empty():
    """Empty results list returns a no-results message containing the query."""
    output = _select_within_budget([], max_chars=30_000, query="myquery")
    assert "No results found for: myquery" in output


def test_select_within_budget_separator():
    """Included results are joined with the expected separator."""
    results = ["result_a", "result_b"]
    output = _select_within_budget(results, max_chars=10_000, query="test")
    assert "---" in output
    assert "result_a" in output
    assert "result_b" in output


def test_select_within_budget_exact_boundary():
    """A single result exactly at the budget boundary is included without truncation."""
    result = "a" * 100
    output = _select_within_budget([result], max_chars=100, query="test")
    assert output == result


def test_select_within_budget_tiny_budget():
    """A very small budget causes even the first result to be truncated."""
    result = "a" * 1000
    output = _select_within_budget([result], max_chars=10, query="test")
    assert "Content truncated" in output


def test_select_within_budget_first_exceeds_budget_multi():
    """First result in a multi-result list exceeding the budget is hard-truncated."""
    big = "z" * 5000
    output = _select_within_budget([big, "small"], max_chars=100, query="test")
    assert "Content truncated" in output
    assert len(output) <= 200
    assert "Budget reached" not in output


# ---------------------------------------------------------------------------
# clean_heading / format_sections
# ---------------------------------------------------------------------------


def test_clean_heading_collapses_nbsp():
    """Solr numbering separators (U+00A0) become ordinary spaces."""
    assert clean_heading("Chapter\u00a09.\u00a0Admission plugins") == "Chapter 9. Admission plugins"


def test_clean_heading_trims_and_collapses_runs():
    """Surrounding and repeated whitespace collapses to single spaces."""
    assert clean_heading("  1.1.  About   OpenShift  ") == "1.1. About OpenShift"


def test_clean_heading_preserves_unicode_text():
    """Non-ASCII headings survive normalisation unchanged."""
    assert clean_heading("インストール概要") == "インストール概要"


def test_format_sections_empty():
    """No headings → empty string."""
    assert format_sections(SolrDoc()) == ""


def test_format_sections_blank_headings_only():
    """Headings that are only whitespace do not produce an empty outline."""
    assert format_sections(SolrDoc(heading_h1=["   ", " "])) == ""


def test_format_sections_lists_h1_and_h2():
    """Chapters are listed first, then sections, both normalised."""
    doc = SolrDoc(
        heading_h1=["Chapter 1. Overview"],
        heading_h2=["1.1. Prerequisites", "1.2. Steps"],
    )
    result = format_sections(doc)
    assert "Sections in this document:" in result
    assert result.index("Chapter 1. Overview") < result.index("1.1. Prerequisites")
    assert "1.2. Steps" in result


def test_format_sections_emits_no_anchor_fragments_without_anchors():
    """Without anchors from the mirror the outline must not invent #fragment links."""
    doc = SolrDoc(heading_h1=["About the installation"], heading_h2=["1.1. Prerequisites"])
    result = format_sections(doc)
    assert "#about-the-installation" not in result
    assert "do not invent a #fragment" in result


def test_format_sections_deduplicates_across_levels():
    """A title indexed as both h1 and h2 is listed once."""
    doc = SolrDoc(heading_h1=["Overview"], heading_h2=["Overview", "Details"])
    result = format_sections(doc)
    assert result.count("Overview") == 1


def test_format_sections_sheds_h2_before_h1_over_budget():
    """Over budget, the chapter list survives and the h2 detail is dropped."""
    doc = SolrDoc(heading_h1=["Chapter A", "Chapter B"], heading_h2=[f"Detail {n}" for n in range(50)])
    result = format_sections(doc, max_chars=60)
    assert "Chapter A" in result
    assert "Chapter B" in result
    assert "Detail 0" not in result
    assert "deeper subsections omitted" in result


def test_format_sections_reports_the_full_total_when_trimmed():
    """The note names how many sections the document really has."""
    doc = SolrDoc(heading_h1=["Chapter A"], heading_h2=[f"Detail {n}" for n in range(9)])
    result = format_sections(doc, max_chars=100)
    assert "of 10 sections" in result


# ---------------------------------------------------------------------------
# format_sections with real anchors from the HTML mirror
# ---------------------------------------------------------------------------

_URL = "https://access.redhat.com/documentation/en-us/guide/index"
_SECTIONS = [
    Section("con-config-tuning-intro-str", "Chapter 1. Kafka tuning overview"),
    Section("mapping_properties_and_values", "1.1. Mapping properties and values"),
]


def test_format_sections_renders_real_anchors():
    """Anchors from the mirror are rendered as #fragment plus the section title."""
    result = format_sections(SolrDoc(), _SECTIONS, url=_URL)
    assert "#con-config-tuning-intro-str — Chapter 1. Kafka tuning overview" in result
    assert "#mapping_properties_and_values — 1.1. Mapping properties and values" in result


def test_format_sections_anchors_show_a_linkable_example():
    """The guidance line demonstrates a URL the caller can copy."""
    result = format_sections(SolrDoc(), _SECTIONS, url=_URL)
    assert f"{_URL}#con-config-tuning-intro-str" in result
    assert "do not invent" not in result


def test_format_sections_anchors_take_precedence_over_headings():
    """Solr headings are the fallback; real anchors win when both are present."""
    doc = SolrDoc(heading_h1=["Stale heading from Solr"])
    result = format_sections(doc, _SECTIONS, url=_URL)
    assert "Stale heading from Solr" not in result


def test_format_sections_falls_back_when_mirror_returns_nothing():
    """An unreachable mirror still yields the title-only outline."""
    doc = SolrDoc(heading_h1=["About the installation"])
    result = format_sections(doc, (), url=_URL)
    assert "About the installation" in result
    assert "do not invent a #fragment" in result


def test_format_sections_keeps_whole_outline_within_budget():
    """A guide-sized outline is shown in full, tail chapters included."""
    sections = [Section("intro", "Chapter 1. Intro", 1)] + [
        Section(f"sec-{n}", f"9.{n}. Late section", 2) for n in range(12)
    ]
    result = format_sections(SolrDoc(), sections, url=_URL)
    assert "#sec-11" in result
    assert "showing" not in result


def test_format_sections_sheds_deepest_level_first_for_anchors():
    """Over budget, chapters keep their anchors and subsections are dropped."""
    sections = [Section(f"ch-{n}", f"Chapter {n}", 1) for n in range(3)] + [
        Section(f"sub-{n}", f"Subsection {n}", 2) for n in range(40)
    ]
    result = format_sections(SolrDoc(), sections, url=_URL, max_chars=150)
    assert "#ch-0" in result
    assert "#ch-2" in result
    assert "#sub-0" not in result
    assert "deeper subsections omitted" in result


def test_format_sections_truncates_when_even_top_level_overflows():
    """A reference page whose every entry is a chapter still gets capped."""
    sections = [Section(f"ch-{n}", f"Chapter {n}", 1) for n in range(200)]
    result = format_sections(SolrDoc(), sections, url=_URL, max_chars=200)
    assert "of 200 sections" in result
    assert "#ch-199" not in result
