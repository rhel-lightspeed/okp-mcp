"""Unit tests for the portal search query builders, intent detection, and chunk conversion."""

import pytest

from okp_mcp.portal import (
    _EOL_PRODUCTS,
    _EUS_HIGHLIGHT_TERMS,
    _FALLBACK_MAX_CHARS,
    _MAIN_QF,
    _VM_HIGHLIGHT_TERMS,
    PortalChunk,
    _apply_intent_boosts,
    _build_deprecation_query,
    _build_eol_filter,
    _build_main_query,
    _deduplicate_by_parent,
    _detect_eus_intent,
    _detect_release_date_intent,
    _detect_vm_intent,
    _docs_to_chunks,
    _fallback_cve,
    _fallback_errata,
    _resolve_title,
)

# ---------------------------------------------------------------------------
# EOL products
# ---------------------------------------------------------------------------


class TestEolProducts:
    """Verify the EOL product set and filter builder."""

    def test_eol_products_is_frozenset(self):
        """_EOL_PRODUCTS is an immutable frozenset."""
        assert isinstance(_EOL_PRODUCTS, frozenset)

    def test_eol_products_contains_known_entries(self):
        """Spot-check that well-known EOL products are present."""
        assert "Red Hat Virtualization" in _EOL_PRODUCTS
        assert "Red Hat Software Collections" in _EOL_PRODUCTS
        assert "Red Hat Decision Manager" in _EOL_PRODUCTS

    def test_eol_products_excludes_active(self):
        """Active products should not appear in the EOL set."""
        assert "Red Hat Enterprise Linux" not in _EOL_PRODUCTS
        assert "Red Hat OpenShift Container Platform" not in _EOL_PRODUCTS

    def test_build_eol_filter_excludes_all_products(self):
        """_build_eol_filter produces an AND-joined fq excluding every EOL product."""
        fq = _build_eol_filter()
        for product in _EOL_PRODUCTS:
            assert f'-product:"{product}"' in fq

    def test_build_eol_filter_uses_and_conjunction(self):
        """Filter clauses are joined with AND so Solr applies all exclusions."""
        fq = _build_eol_filter()
        # With 25 products there should be 24 AND separators
        assert fq.count(" AND ") == len(_EOL_PRODUCTS) - 1


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectVmIntent:
    """Verify VM/virtualization intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "create a vm on RHEL 9",
            "virtual machine performance tuning",
            "virtualization best practices",
            "running vms on rhel",
            "hypervisor configuration",
        ],
        ids=["vm", "virtual-machine", "virtualization", "vms", "hypervisor"],
    )
    def test_positive(self, query: str):
        """Queries containing VM keywords trigger VM intent."""
        assert _detect_vm_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "configure firewall rhel 9",
            "kernel tuning",
            "",
        ],
        ids=["firewall", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries without VM keywords do not trigger VM intent."""
        assert _detect_vm_intent(query) is False


class TestDetectReleaseDateIntent:
    """Verify release-date intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "rhel 9 release date",
            "when was rhel 8 released",
            "general availability of rhel 10",
        ],
        ids=["release-date", "when-was-released", "general-availability"],
    )
    def test_positive(self, query: str):
        """Queries about release timing trigger release-date intent."""
        assert _detect_release_date_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "install rhel 9",
            "kernel updates",
            "",
        ],
        ids=["install", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries not about release dates do not trigger the intent."""
        assert _detect_release_date_intent(query) is False


class TestDetectEusIntent:
    """Verify EUS (Extended Update Support) intent detection."""

    @pytest.mark.parametrize(
        "query",
        [
            "what is eus support",
            "rhel 9 eus lifecycle",
            "extended update support policy",
        ],
        ids=["eus-keyword", "eus-lifecycle", "extended-update-support"],
    )
    def test_positive(self, query: str):
        """Queries mentioning EUS or Extended Update Support trigger EUS intent."""
        assert _detect_eus_intent(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "configure firewall",
            "kernel updates rhel 9",
            "",
        ],
        ids=["firewall", "kernel", "empty"],
    )
    def test_negative(self, query: str):
        """Queries without EUS keywords do not trigger EUS intent."""
        assert _detect_eus_intent(query) is False


# ---------------------------------------------------------------------------
# Main query builder
# ---------------------------------------------------------------------------


class TestBuildMainQuery:
    """Verify the unified main query parameter builder."""

    def test_query_is_set(self):
        """The cleaned query text appears as the Solr q parameter."""
        params = _build_main_query("kernel panic rhel 9")
        assert params["q"] == "kernel panic rhel 9"

    def test_no_document_kind_filter(self):
        """Main query must NOT restrict documentKind so all types are searched."""
        params = _build_main_query("test query")
        fq = params["fq"]
        # fq is a string (EOL filter only), not a list with documentKind
        assert isinstance(fq, str)
        assert "documentKind" not in fq

    def test_custom_qf_includes_portal_synopsis(self):
        """Custom qf adds portal_synopsis boost for errata scoring."""
        params = _build_main_query("test")
        assert params["qf"] == _MAIN_QF
        assert "portal_synopsis^3" in params["qf"]

    def test_custom_qf_excludes_cve_details(self):
        """cve_details is a string field and must not appear in qf."""
        params = _build_main_query("test")
        assert "cve_details" not in params["qf"]

    def test_fl_includes_type_specific_fields(self):
        """fl includes CVE and errata fields for chunk-conversion fallback."""
        params = _build_main_query("test")
        fl = params["fl"]
        assert "cve_details" in fl
        assert "cve_threatSeverity" in fl
        assert "portal_synopsis" in fl
        assert "portal_advisory_type" in fl

    def test_rows_overfetch(self):
        """rows=20 over-fetches for diversity after parent deduplication."""
        params = _build_main_query("test")
        assert params["rows"] == 20

    def test_hl_default_summary_false(self):
        """defaultSummary is disabled so CVE/errata boilerplate triggers fallback."""
        params = _build_main_query("test")
        assert params["hl.defaultSummary"] == "false"

    def test_recency_boost(self):
        """A recency bf boost is applied to favor newer content."""
        params = _build_main_query("test")
        assert "bf" in params
        assert "recip(ms(NOW,lastModifiedDate)" in params["bf"]

    def test_eol_filter_applied(self):
        """EOL products are excluded via fq."""
        params = _build_main_query("test")
        for product in ["Red Hat Virtualization", "Red Hat Software Collections"]:
            assert f'-product:"{product}"' in params["fq"]

    def test_highlight_snippets(self):
        """6 highlight snippets requested per document."""
        params = _build_main_query("test")
        assert params["hl.snippets"] == "6"


# ---------------------------------------------------------------------------
# Deprecation query builder
# ---------------------------------------------------------------------------


class TestBuildDeprecationQuery:
    """Verify the deprecation-focused query parameter builder."""

    def test_query_appends_deprecation_terms(self):
        """'deprecated removed' is appended to the user query."""
        params = _build_deprecation_query("virt-manager rhel 9")
        assert params["q"] == "virt-manager rhel 9 deprecated removed"

    def test_restricted_to_docs_solutions_articles(self):
        """Only docs, solutions, and articles are searched (no CVEs/errata)."""
        params = _build_deprecation_query("test")
        fq_list = params["fq"]
        assert isinstance(fq_list, list)
        kind_fq = fq_list[0]
        assert "documentKind:(solution OR article OR documentation)" in kind_fq

    def test_eol_filter_applied(self):
        """EOL products are excluded from the deprecation query too."""
        params = _build_deprecation_query("test")
        fq_list = params["fq"]
        eol_fq = fq_list[1]
        assert '-product:"Red Hat Virtualization"' in eol_fq

    def test_deprecation_bq_boosts(self):
        """bq heavily boosts deprecation-related title and content terms."""
        params = _build_deprecation_query("test")
        bq = params["bq"]
        assert "deprecated" in bq
        assert "removed" in bq
        assert "release notes" in bq
        assert "considerations in adopting" in bq

    def test_fewer_rows_than_main(self):
        """Deprecation query fetches fewer rows (5) than the main query (20)."""
        params = _build_deprecation_query("test")
        assert params["rows"] == 5

    def test_fewer_highlight_snippets(self):
        """4 highlight snippets per doc (vs 6 in main query)."""
        params = _build_deprecation_query("test")
        assert params["hl.snippets"] == "4"

    def test_fl_excludes_cve_fields(self):
        """Deprecation fl does not include CVE/errata-specific fields."""
        params = _build_deprecation_query("test")
        fl = params["fl"]
        assert "cve_details" not in fl
        assert "portal_synopsis" not in fl


# ---------------------------------------------------------------------------
# Intent boost application
# ---------------------------------------------------------------------------


class TestApplyIntentBoosts:
    """Verify that _apply_intent_boosts mutates params correctly for each intent."""

    def test_vm_intent_adds_bq_and_hlq(self):
        """VM intent injects cockpit/virt-manager bq and expands hl.q."""
        params = _build_main_query("create a vm")
        _apply_intent_boosts(params, "create a vm", "create vm")
        assert "cockpit" in params["bq"]
        assert "virt-manager" in params["bq"]
        assert _VM_HIGHLIGHT_TERMS in params["hl.q"]
        assert params["hl.q"].startswith("create vm")

    def test_eus_intent_adds_bq_and_hlq(self):
        """EUS intent injects Enhanced EUS bq and expands hl.q."""
        params = _build_main_query("eus support")
        _apply_intent_boosts(params, "eus support", "eus support")
        assert "Enhanced EUS" in params["bq"]
        assert "EUS FAQ" in params["bq"]
        assert _EUS_HIGHLIGHT_TERMS in params["hl.q"]

    def test_release_date_intent_adds_bq(self):
        """Release-date intent injects release dates bq."""
        params = _build_main_query("when was rhel 9 released")
        _apply_intent_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "Enterprise Linux Release Dates" in params["bq"]
        assert 'allTitle:"release dates"' in params["bq"]

    def test_release_date_intent_no_hlq(self):
        """Release-date intent does not override hl.q (no special highlight terms)."""
        params = _build_main_query("when was rhel 9 released")
        _apply_intent_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "hl.q" not in params

    def test_no_intent_leaves_params_unchanged(self):
        """Params are not mutated when no intent is detected."""
        params = _build_main_query("configure firewall rhel 9")
        original = dict(params)
        _apply_intent_boosts(params, "configure firewall rhel 9", "configure firewall rhel 9")
        assert params == original

    @pytest.mark.parametrize(
        "query",
        ["nvme tuning rhel 9", "jvm heap configuration", "evms partition"],
        ids=["nvme", "jvm", "evms"],
    )
    def test_vm_intent_no_false_positive(self, query):
        """Substrings containing 'vm' (nvme, jvm, evms) must not trigger VM intent."""
        assert not _detect_vm_intent(query)

    def test_eus_intent_no_false_positive(self):
        """Substrings containing 'eus' (e.g. 'zeus') must not trigger EUS intent."""
        assert not _detect_eus_intent("zeus cluster setup")

    def test_release_date_intent_no_false_positive(self):
        """Substrings containing 'released' fragment must not trigger release-date intent."""
        assert not _detect_release_date_intent("unreleased feature flag")

    def test_eus_overrides_vm_when_both_match(self):
        """When both VM and EUS match, EUS runs second and overwrites bq/hl.q."""
        # "eus" is a substring that doesn't match VM intent, but test the
        # override behavior when both would match (e.g., "virtualization eus").
        params = _build_main_query("virtualization eus")
        _apply_intent_boosts(params, "virtualization eus", "virtualization eus")
        # EUS runs after VM, so it wins
        assert "Enhanced EUS" in params["bq"]
        assert _EUS_HIGHLIGHT_TERMS in params["hl.q"]

    def test_mutates_in_place(self):
        """_apply_intent_boosts modifies the dict in-place, returns None."""
        params = _build_main_query("eus policy")
        result = _apply_intent_boosts(params, "eus policy", "eus policy")
        assert result is None
        assert "bq" in params


# ---------------------------------------------------------------------------
# Helper fixtures for chunk conversion tests
# ---------------------------------------------------------------------------


def _make_solr_response(docs, highlighting=None):
    """Build a minimal Solr response dict for testing."""
    return {
        "response": {"numFound": len(docs), "docs": docs},
        "highlighting": highlighting or {},
    }


def _make_doc(doc_id="doc1", kind="documentation", **overrides):
    """Build a minimal Solr document dict."""
    base = {
        "id": doc_id,
        "allTitle": f"Title for {doc_id}",
        "view_uri": f"/documentation/en-US/{doc_id}",
        "documentKind": kind,
        "product": "Red Hat Enterprise Linux",
        "score": 10.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Title resolution
# ---------------------------------------------------------------------------


class TestResolveTitle:
    """Verify title resolution priority: allTitle > title > heading_h1 > id."""

    def test_prefers_all_title(self):
        """allTitle is used when available."""
        assert _resolve_title({"allTitle": "Best Title", "title": "Alt"}) == "Best Title"

    def test_falls_back_to_title(self):
        """title is used when allTitle is missing."""
        assert _resolve_title({"title": "Fallback Title"}) == "Fallback Title"

    def test_falls_back_to_heading_h1(self):
        """First heading_h1 entry is used when allTitle and title are missing."""
        assert _resolve_title({"heading_h1": ["H1 Title", "Other"]}) == "H1 Title"

    def test_falls_back_to_id(self):
        """Document id is used as last resort."""
        assert _resolve_title({"id": "doc-123"}) == "doc-123"

    def test_empty_doc_returns_untitled(self):
        """Empty dict returns 'Untitled'."""
        assert _resolve_title({}) == "Untitled"


# ---------------------------------------------------------------------------
# Chunk conversion: highlighted documents
# ---------------------------------------------------------------------------


class TestDocsToChunksHighlights:
    """Verify chunk conversion for documents with highlight snippets."""

    def test_each_snippet_becomes_a_chunk(self):
        """Each highlight snippet produces a separate PortalChunk."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["Snippet one.", "Snippet two.", "Snippet three."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test query")
        assert len(chunks) == 3
        assert chunks[0].chunk == "Snippet one."
        assert chunks[1].chunk == "Snippet two."
        assert chunks[2].chunk == "Snippet three."

    def test_chunk_ids_are_unique(self):
        """Each chunk gets a unique doc_id based on parent + snippet index."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["A.", "B."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].doc_id == "doc1_hl_0"
        assert chunks[1].doc_id == "doc1_hl_1"

    def test_parent_id_links_to_source(self):
        """All chunks from the same doc share the parent_id."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["A.", "B."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert all(c.parent_id == "doc1" for c in chunks)

    def test_strips_html_tags(self):
        """HTML markup from Solr highlighting is removed."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["Use <em>cockpit</em> for <b>VM</b> management."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].chunk == "Use cockpit for VM management."

    def test_num_tokens_counted(self):
        """num_tokens reflects whitespace-split word count of cleaned text."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["one two three four"]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].num_tokens == 4

    def test_url_constructed_from_view_uri(self):
        """online_source_url uses doc_uri() to build the access.redhat.com URL."""
        doc = _make_doc(view_uri="/documentation/en-US/some/page")
        hl = {doc["id"]: {"main_content": ["Content."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].online_source_url == "https://access.redhat.com/documentation/en-US/some/page"

    def test_document_kind_preserved(self):
        """documentKind from the Solr doc is carried to each chunk."""
        doc = _make_doc(kind="solution")
        hl = {doc["id"]: {"main_content": ["Fix the issue."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].documentKind == "solution"

    def test_score_preserved(self):
        """Solr relevance score is carried to each chunk."""
        doc = _make_doc(score=42.5)
        hl = {doc["id"]: {"main_content": ["Content."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert chunks[0].score == 42.5

    def test_rhv_filtering_applied(self):
        """RHV-contaminated sentences are removed from highlight snippets."""
        doc = _make_doc()
        # "fully supported" + "rhv" triggers RHV contamination filter
        hl = {
            doc["id"]: {
                "main_content": ["SPICE is still fully supported in RHV deployments. Use cockpit for management."]
            }
        }
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "cockpit management")
        # The RHV sentence should be filtered out, keeping only the cockpit sentence
        assert "RHV" not in chunks[0].chunk
        assert "cockpit" in chunks[0].chunk

    def test_rhv_filtering_skipped_for_rhv_query(self):
        """RHV filtering is disabled when the query itself mentions RHV."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["SPICE is still fully supported in RHV deployments."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "rhv spice support")
        assert "RHV" in chunks[0].chunk

    def test_empty_snippet_after_stripping_is_skipped(self):
        """Snippets that become empty after HTML stripping are excluded."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["<em></em>", "Real content here."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "test")
        assert len(chunks) == 1
        assert chunks[0].chunk == "Real content here."


# ---------------------------------------------------------------------------
# Chunk conversion: fallback (no highlights)
# ---------------------------------------------------------------------------


class TestDocsToChunksFallback:
    """Verify fallback chunk conversion when highlighting returns no snippets."""

    def test_cve_uses_cve_details(self):
        """CVE without highlights falls back to cve_details field."""
        doc = _make_doc(
            doc_id="CVE-2024-12345",
            kind="Cve",
            allTitle="CVE-2024-12345",
            view_uri="/security/cve/CVE-2024-12345",
            cve_details="Buffer overflow in libfoo allows remote code execution.",
            cve_threatSeverity="Important",
        )
        chunks = _docs_to_chunks(_make_solr_response([doc]), "libfoo vulnerability")
        assert len(chunks) == 1
        assert "Buffer overflow" in chunks[0].chunk
        assert "Important" in chunks[0].chunk
        assert chunks[0].doc_id == "CVE-2024-12345_fb_0"

    def test_cve_without_severity(self):
        """CVE fallback works without severity data."""
        doc = _make_doc(kind="Cve", cve_details="Some vulnerability details.")
        chunks = _docs_to_chunks(_make_solr_response([doc]), "test")
        assert len(chunks) == 1
        assert "Some vulnerability details" in chunks[0].chunk
        assert "Severity" not in chunks[0].chunk

    def test_errata_uses_synopsis(self):
        """Erratum without highlights falls back to portal_synopsis."""
        doc = _make_doc(
            doc_id="RHSA-2024:1234",
            kind="Erratum",
            allTitle="RHSA-2024:1234",
            view_uri="/errata/RHSA-2024:1234",
            portal_synopsis="Important: kernel security update",
            portal_advisory_type="Security Advisory",
            portal_severity="Important",
        )
        chunks = _docs_to_chunks(_make_solr_response([doc]), "kernel security")
        assert len(chunks) == 1
        assert "kernel security update" in chunks[0].chunk
        assert "Security Advisory" in chunks[0].chunk

    def test_errata_includes_summary_within_budget(self):
        """Erratum fallback includes portal_summary when space permits."""
        doc = _make_doc(
            kind="Erratum",
            portal_synopsis="Synopsis text.",
            portal_summary="Detailed summary content here.",
        )
        chunks = _docs_to_chunks(_make_solr_response([doc]), "test")
        assert "Synopsis text" in chunks[0].chunk
        assert "Detailed summary" in chunks[0].chunk

    def test_generic_doc_uses_main_content(self):
        """Documentation without highlights falls back to first 600 chars of main_content."""
        long_content = "Important configuration details. " * 50
        doc = _make_doc(main_content=long_content)
        chunks = _docs_to_chunks(_make_solr_response([doc]), "test")
        assert len(chunks) == 1
        assert len(chunks[0].chunk) <= _FALLBACK_MAX_CHARS

    def test_doc_without_any_content_produces_no_chunk(self):
        """Documents with no highlights and no fallback fields are skipped."""
        doc = _make_doc(kind="Cve")  # CVE without cve_details or severity
        chunks = _docs_to_chunks(_make_solr_response([doc]), "test")
        assert len(chunks) == 0


# ---------------------------------------------------------------------------
# Chunk conversion: edge cases
# ---------------------------------------------------------------------------


class TestDocsToChunksEdgeCases:
    """Verify edge cases in chunk conversion."""

    def test_empty_response(self):
        """Empty Solr response produces no chunks."""
        chunks = _docs_to_chunks(_make_solr_response([]), "test")
        assert chunks == []

    def test_missing_highlighting_key(self):
        """Response without highlighting key uses fallback for all docs."""
        doc = _make_doc(main_content="Some content here.")
        response = {"response": {"numFound": 1, "docs": [doc]}}
        chunks = _docs_to_chunks(response, "test")
        assert len(chunks) == 1
        assert chunks[0].doc_id.endswith("_fb_0")

    def test_multiple_docs_interleaved(self):
        """Multiple docs produce chunks in document order."""
        doc_a = _make_doc(doc_id="a", allTitle="Doc A")
        doc_b = _make_doc(doc_id="b", allTitle="Doc B")
        hl = {
            "a": {"main_content": ["A content."]},
            "b": {"main_content": ["B content."]},
        }
        chunks = _docs_to_chunks(_make_solr_response([doc_a, doc_b], hl), "test")
        assert len(chunks) == 2
        assert chunks[0].title == "Doc A"
        assert chunks[1].title == "Doc B"

    def test_empty_query_skips_rhv_filter(self):
        """Empty query string skips RHV filtering entirely."""
        doc = _make_doc()
        hl = {doc["id"]: {"main_content": ["SPICE is fully supported in RHV deployments."]}}
        chunks = _docs_to_chunks(_make_solr_response([doc], hl), "")
        # No RHV filtering with empty query
        assert "RHV" in chunks[0].chunk


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------


class TestFallbackCve:
    """Verify CVE-specific fallback text construction."""

    def test_severity_and_details(self):
        """Both severity and details are included."""
        text = _fallback_cve({"cve_threatSeverity": "Critical", "cve_details": "RCE via buffer overflow."})
        assert "Critical" in text
        assert "RCE via buffer overflow" in text

    def test_details_truncated_at_budget(self):
        """Long cve_details are truncated to _FALLBACK_MAX_CHARS."""
        long_details = "x" * 1000
        text = _fallback_cve({"cve_details": long_details})
        assert len(text) <= _FALLBACK_MAX_CHARS

    def test_empty_fields_returns_empty(self):
        """CVE with no severity or details returns empty string."""
        assert _fallback_cve({}) == ""


class TestFallbackErrata:
    """Verify errata-specific fallback text construction."""

    def test_full_metadata(self):
        """Advisory type, severity, synopsis, and summary all appear."""
        text = _fallback_errata(
            {
                "portal_advisory_type": "Security Advisory",
                "portal_severity": "Important",
                "portal_synopsis": "kernel security update",
                "portal_summary": "Fixes multiple CVEs.",
            }
        )
        assert "Security Advisory" in text
        assert "Important" in text
        assert "kernel security update" in text
        assert "Fixes multiple CVEs" in text

    def test_synopsis_only(self):
        """Works with just portal_synopsis."""
        text = _fallback_errata({"portal_synopsis": "Bug fix update"})
        assert "Bug fix update" in text

    def test_empty_returns_empty(self):
        """No errata fields returns empty string."""
        assert _fallback_errata({}) == ""


# ---------------------------------------------------------------------------
# Parent deduplication
# ---------------------------------------------------------------------------


class TestDeduplicateByParent:
    """Verify parent-level deduplication of chunks."""

    def test_keeps_first_chunk_per_parent(self):
        """Only the highest-ranked (first) chunk per parent_id is kept."""
        chunks = [
            PortalChunk(doc_id="d1_hl_0", parent_id="d1", chunk="Best match.", chunk_index=0),
            PortalChunk(doc_id="d1_hl_1", parent_id="d1", chunk="Second match.", chunk_index=1),
            PortalChunk(doc_id="d1_hl_2", parent_id="d1", chunk="Third match.", chunk_index=2),
        ]
        result = _deduplicate_by_parent(chunks)
        assert len(result) == 1
        assert result[0].doc_id == "d1_hl_0"

    def test_multiple_parents_each_kept(self):
        """Different parent_ids each contribute one chunk."""
        chunks = [
            PortalChunk(doc_id="a_hl_0", parent_id="a", chunk="A content."),
            PortalChunk(doc_id="b_hl_0", parent_id="b", chunk="B content."),
            PortalChunk(doc_id="a_hl_1", parent_id="a", chunk="A second."),
        ]
        result = _deduplicate_by_parent(chunks)
        assert len(result) == 2
        assert result[0].doc_id == "a_hl_0"
        assert result[1].doc_id == "b_hl_0"

    def test_preserves_rank_order(self):
        """Output order matches input order (rank preservation)."""
        chunks = [
            PortalChunk(doc_id="x_hl_0", parent_id="x", chunk="X."),
            PortalChunk(doc_id="y_hl_0", parent_id="y", chunk="Y."),
            PortalChunk(doc_id="z_hl_0", parent_id="z", chunk="Z."),
            PortalChunk(doc_id="x_hl_1", parent_id="x", chunk="X2."),
        ]
        result = _deduplicate_by_parent(chunks)
        assert [c.doc_id for c in result] == ["x_hl_0", "y_hl_0", "z_hl_0"]

    def test_orphan_chunks_always_kept(self):
        """Chunks with parent_id=None are treated as unique."""
        chunks = [
            PortalChunk(doc_id="orphan1", parent_id=None, chunk="O1."),
            PortalChunk(doc_id="orphan2", parent_id=None, chunk="O2."),
        ]
        result = _deduplicate_by_parent(chunks)
        assert len(result) == 2

    def test_mixed_orphans_and_parents(self):
        """Orphans and parent-grouped chunks coexist correctly."""
        chunks = [
            PortalChunk(doc_id="a_hl_0", parent_id="a", chunk="A."),
            PortalChunk(doc_id="orphan", parent_id=None, chunk="O."),
            PortalChunk(doc_id="a_hl_1", parent_id="a", chunk="A2."),
        ]
        result = _deduplicate_by_parent(chunks)
        assert len(result) == 2
        assert result[0].doc_id == "a_hl_0"
        assert result[1].doc_id == "orphan"

    def test_empty_input(self):
        """Empty list returns empty list."""
        assert _deduplicate_by_parent([]) == []

    def test_single_chunk(self):
        """Single chunk is returned as-is."""
        chunks = [PortalChunk(doc_id="solo", parent_id="p1", chunk="Only one.")]
        result = _deduplicate_by_parent(chunks)
        assert len(result) == 1
        assert result[0].doc_id == "solo"
