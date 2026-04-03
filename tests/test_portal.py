"""Unit tests for the portal search module: query builders, chunk conversion, RRF, orchestrator, formatting."""

import httpx
import pytest
import respx

from okp_mcp.intent import INTENT_RULES, IntentRule, apply_deprecation_boosts, apply_main_boosts
from okp_mcp.portal import (
    _DEPRECATION_WARNING,
    _EOL_PRODUCTS,
    _FALLBACK_MAX_CHARS,
    _KIND_FACET_LABELS,
    _KIND_LABELS,
    _MAIN_QF,
    PortalChunk,
    _build_deprecation_query,
    _build_eol_filter,
    _build_main_query,
    _deduplicate_by_parent,
    _docs_to_chunks,
    _extract_facet_counts,
    _fallback_cve,
    _fallback_errata,
    _format_facet_summary,
    _format_portal_chunk,
    _format_portal_results,
    _reciprocal_rank_fusion,
    _resolve_title,
    _run_portal_search,
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


def _get_rule(name: str) -> IntentRule:
    """Look up an IntentRule by name for testing."""
    for rule in INTENT_RULES:
        if rule.name == name:
            return rule
    raise ValueError(f"No intent rule named {name!r}")


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
        assert _get_rule("vm").matches(query) is True

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
        assert _get_rule("vm").matches(query) is False


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
        assert _get_rule("release_date").matches(query) is True

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
        assert _get_rule("release_date").matches(query) is False


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
        assert _get_rule("eus").matches(query) is True

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
        assert _get_rule("eus").matches(query) is False


class TestDetectSpiceIntent:
    """Verify SPICE display protocol intent detection.

    SPICE queries are about the display protocol, not VM management.
    Detecting SPICE separately prevents the generic VM intent from injecting
    cockpit/virsh boosts that drown out VNC replacement information.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "is spice available for rhel vms",
            "spice protocol deprecated",
            "how to use spice with virtualization",
            "spice remote display",
        ],
        ids=["spice-vms", "spice-deprecated", "spice-virtualization", "spice-display"],
    )
    def test_positive(self, query: str):
        """Queries mentioning SPICE trigger SPICE intent."""
        assert _get_rule("spice").matches(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            "configure firewall",
            "create a vm on rhel 9",
            "vnc remote display",
            "",
        ],
        ids=["firewall", "vm-no-spice", "vnc-only", "empty"],
    )
    def test_negative(self, query: str):
        """Queries without SPICE keyword do not trigger SPICE intent."""
        assert _get_rule("spice").matches(query) is False


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
        """rows=10 over-fetches for diversity after parent deduplication."""
        params = _build_main_query("test")
        assert params["rows"] == 10

    def test_hl_default_summary_true(self):
        """defaultSummary is enabled; CVE/errata boilerplate is handled in _docs_to_chunks."""
        params = _build_main_query("test")
        assert params["hl.defaultSummary"] == "true"

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
        """3 highlight snippets requested per document (enough for BM25 to pick good passages)."""
        params = _build_main_query("test")
        assert params["hl.snippets"] == "3"


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
        """Deprecation query fetches fewer rows (3) than the main query (10)."""
        params = _build_deprecation_query("test")
        assert params["rows"] == 3

    def test_fewer_highlight_snippets(self):
        """2 highlight snippets per doc (only needs to detect deprecation signals)."""
        params = _build_deprecation_query("test")
        assert params["hl.snippets"] == "2"

    def test_fl_excludes_cve_fields(self):
        """Deprecation fl does not include CVE/errata-specific fields."""
        params = _build_deprecation_query("test")
        fl = params["fl"]
        assert "cve_details" not in fl
        assert "portal_synopsis" not in fl


# ---------------------------------------------------------------------------
# Intent boost application
# ---------------------------------------------------------------------------


class TestApplyMainBoosts:
    """Verify that apply_main_boosts mutates params correctly for each intent."""

    def test_vm_intent_adds_bq_and_hlq(self):
        """VM intent injects cockpit/virt-manager bq and expands hl.q."""
        params = _build_main_query("create a vm")
        apply_main_boosts(params, "create a vm", "create vm")
        assert "cockpit" in params["bq"]
        assert "virt-manager" in params["bq"]
        assert _get_rule("vm").highlight_terms in params["hl.q"]
        assert params["hl.q"].startswith("create vm")

    def test_eus_intent_adds_bq_and_hlq(self):
        """EUS intent injects Enhanced EUS bq and expands hl.q."""
        params = _build_main_query("eus support")
        apply_main_boosts(params, "eus support", "eus support")
        assert "Enhanced EUS" in params["bq"]
        assert "EUS FAQ" in params["bq"]
        assert _get_rule("eus").highlight_terms in params["hl.q"]

    def test_release_date_intent_adds_bq(self):
        """Release-date intent injects release dates bq."""
        params = _build_main_query("when was rhel 9 released")
        apply_main_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "Enterprise Linux Release Dates" in params["bq"]
        assert 'allTitle:"release dates"' in params["bq"]

    def test_release_date_intent_no_hlq(self):
        """Release-date intent does not override hl.q (no special highlight terms)."""
        params = _build_main_query("when was rhel 9 released")
        apply_main_boosts(params, "when was rhel 9 released", "rhel 9 released")
        assert "hl.q" not in params

    def test_no_intent_leaves_params_unchanged(self):
        """Params are not mutated when no intent is detected."""
        params = _build_main_query("configure firewall rhel 9")
        original = dict(params)
        apply_main_boosts(params, "configure firewall rhel 9", "configure firewall rhel 9")
        assert params == original

    @pytest.mark.parametrize(
        "query",
        ["nvme tuning rhel 9", "jvm heap configuration", "evms partition"],
        ids=["nvme", "jvm", "evms"],
    )
    def test_vm_intent_no_false_positive(self, query):
        """Substrings containing 'vm' (nvme, jvm, evms) must not trigger VM intent."""
        assert not _get_rule("vm").matches(query)

    def test_eus_intent_no_false_positive(self):
        """Substrings containing 'eus' (e.g. 'zeus') must not trigger EUS intent."""
        assert not _get_rule("eus").matches("zeus cluster setup")

    def test_release_date_intent_no_false_positive(self):
        """Substrings containing 'released' fragment must not trigger release-date intent."""
        assert not _get_rule("release_date").matches("unreleased feature flag")

    def test_spice_intent_adds_bq_and_hlq(self):
        """SPICE intent injects VNC/deprecation bq and expands hl.q with SPICE terms."""
        params = _build_main_query("spice rhel")
        apply_main_boosts(params, "spice rhel", "spice rhel")
        assert "VNC" in params["bq"]
        assert "spice" in params["bq"]
        assert _get_rule("spice").highlight_terms in params["hl.q"]
        assert params["hl.q"].startswith("spice rhel")

    def test_spice_overrides_vm_when_both_match(self):
        """SPICE intent wins over VM intent for queries like 'SPICE for VMs'.

        SPICE questions are about the display protocol, not VM management.
        Without this override, cockpit/virsh highlight terms flood results
        and the LLM omits the VNC replacement.  See RSPEED_2481.
        """
        params = _build_main_query("spice rhel vms")
        apply_main_boosts(params, "is spice available for rhel vms", "spice rhel vms")
        # SPICE is more specific, so it matches first and wins
        assert "VNC" in params["bq"]
        assert _get_rule("spice").highlight_terms in params["hl.q"]
        # VM boosts must NOT survive
        assert "cockpit" not in params["bq"]
        assert "virt-manager" not in params["bq"]

    def test_eus_overrides_vm_when_both_match(self):
        """When both VM and EUS match, EUS is more specific and wins."""
        params = _build_main_query("virtualization eus")
        apply_main_boosts(params, "virtualization eus", "virtualization eus")
        # EUS is higher priority (more specific), so it wins
        assert "Enhanced EUS" in params["bq"]
        assert _get_rule("eus").highlight_terms in params["hl.q"]

    def test_mutates_in_place(self):
        """apply_main_boosts modifies the dict in-place, returns None."""
        params = _build_main_query("eus policy")
        result = apply_main_boosts(params, "eus policy", "eus policy")
        assert result is None
        assert "bq" in params


# ---------------------------------------------------------------------------
# Intent boost logging
# ---------------------------------------------------------------------------


class TestIntentBoostLogging:
    """Verify that intent boost functions emit info-level log lines."""

    @pytest.mark.parametrize(
        ("query", "expected_fragment"),
        [
            ("create a vm", "applied 'vm' to main query (bq + hl.q)"),
            ("when was rhel 9 released", "applied 'release_date' to main query (bq)"),
            ("spice rhel", "applied 'spice' to main query (bq + hl.q)"),
        ],
        ids=["vm-bq-hlq", "release-date-bq-only", "spice-bq-hlq"],
    )
    def test_main_boost_logs(self, caplog, query, expected_fragment):
        """Main query boost logs intent name and which params were set."""
        params = _build_main_query(query)
        with caplog.at_level("INFO", logger="okp_mcp"):
            apply_main_boosts(params, query, query)
        assert any(expected_fragment in m for m in caplog.messages)

    def test_main_boost_no_match_no_log(self, caplog):
        """No log line emitted when no intent matches."""
        params = _build_main_query("configure firewall rhel 9")
        with caplog.at_level("INFO", logger="okp_mcp"):
            apply_main_boosts(params, "configure firewall rhel 9", "configure firewall rhel 9")
        assert not any("Intent boost" in m for m in caplog.messages)

    def test_deprecation_boost_logs_with_weights(self, caplog):
        """Deprecation query boost logs intent name and boost weights."""
        params = _build_deprecation_query("spice rhel")
        with caplog.at_level("INFO", logger="okp_mcp"):
            apply_deprecation_boosts(params, "spice rhel")
        assert any("applied 'spice' to deprecation query (^5/^3)" in m for m in caplog.messages)

    @pytest.mark.parametrize(
        "query",
        ["configure firewall rhel 9", "when was rhel 9 released"],
        ids=["no-match", "intent-without-dep-terms"],
    )
    def test_deprecation_boost_no_log(self, caplog, query):
        """No log line when no deprecation intent matches or matched intent has no dep terms."""
        params = _build_deprecation_query(query)
        with caplog.at_level("INFO", logger="okp_mcp"):
            apply_deprecation_boosts(params, query)
        assert not any("Intent boost" in m for m in caplog.messages)


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


# ---------------------------------------------------------------------------
# Reciprocal rank fusion
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    """Verify standalone RRF merging for PortalChunks."""

    def test_overlapping_docs_boosted(self):
        """Chunks appearing in both lists get higher RRF scores."""
        list_a = [
            PortalChunk(doc_id="shared", parent_id="p1", chunk="A."),
            PortalChunk(doc_id="only_a", parent_id="p2", chunk="B."),
        ]
        list_b = [
            PortalChunk(doc_id="shared", parent_id="p1", chunk="A."),
            PortalChunk(doc_id="only_b", parent_id="p3", chunk="C."),
        ]
        result = _reciprocal_rank_fusion(list_a, list_b, k=60)
        assert result[0].doc_id == "shared"
        assert result[0].rrf_score > result[1].rrf_score

    def test_disjoint_lists_all_appear(self):
        """All chunks from disjoint lists appear in the output."""
        list_a = [PortalChunk(doc_id="a", chunk="A.")]
        list_b = [PortalChunk(doc_id="b", chunk="B.")]
        result = _reciprocal_rank_fusion(list_a, list_b)
        ids = {c.doc_id for c in result}
        assert ids == {"a", "b"}

    def test_empty_inputs(self):
        """No inputs returns empty list."""
        assert _reciprocal_rank_fusion() == []

    def test_single_list_passthrough(self):
        """Single list returns scored copies."""
        chunks = [PortalChunk(doc_id="x", chunk="X.")]
        result = _reciprocal_rank_fusion(chunks)
        assert len(result) == 1
        assert result[0].doc_id == "x"
        assert result[0].rrf_score is not None

    def test_preserves_chunk_data(self):
        """RRF preserves all fields from the selected chunk."""
        chunk = PortalChunk(
            doc_id="d1",
            parent_id="p1",
            title="My Title",
            chunk="Content.",
            documentKind="documentation",
            online_source_url="https://example.com",
        )
        result = _reciprocal_rank_fusion([chunk])
        assert result[0].title == "My Title"
        assert result[0].online_source_url == "https://example.com"

    def test_later_list_chunk_preferred_on_collision(self):
        """When two lists share a doc_id, the chunk from the later list wins."""
        early = [PortalChunk(doc_id="shared", chunk="main snippet")]
        late = [PortalChunk(doc_id="shared", chunk="deprecation snippet")]
        result = _reciprocal_rank_fusion(early, late)
        assert len(result) == 1
        assert result[0].chunk == "deprecation snippet"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


class TestFormatPortalChunk:
    """Verify per-chunk markdown formatting."""

    def test_documentation_chunk(self):
        """Documentation chunk renders with type and URL."""
        chunk = PortalChunk(
            doc_id="d1",
            title="Configuring Firewalls",
            chunk="Use firewalld to manage zones.",
            documentKind="documentation",
            online_source_url="https://access.redhat.com/documentation/en-US/firewall",
        )
        text, _sort_key = _format_portal_chunk(chunk)
        assert "**Configuring Firewalls**" in text
        assert "Type: Documentation" in text
        assert "https://access.redhat.com/documentation/en-US/firewall" in text
        assert "Use firewalld" in text

    def test_cve_chunk(self):
        """CVE chunk renders with CVE type label."""
        chunk = PortalChunk(
            doc_id="cve1",
            title="CVE-2024-12345",
            chunk="Buffer overflow in libfoo.",
            documentKind="Cve",
            online_source_url="https://access.redhat.com/security/cve/CVE-2024-12345",
        )
        text, _ = _format_portal_chunk(chunk)
        assert "Type: CVE" in text
        assert "CVE-2024-12345" in text

    def test_errata_chunk(self):
        """Erratum chunk renders with Security Advisory label."""
        chunk = PortalChunk(
            doc_id="e1",
            title="RHSA-2024:1234",
            chunk="Important: kernel security update.",
            documentKind="Erratum",
        )
        text, _ = _format_portal_chunk(chunk)
        assert "Type: Security Advisory" in text

    def test_deprecation_detected(self):
        """Chunk mentioning deprecation gets annotation and sort_key <= 0."""
        chunk = PortalChunk(
            doc_id="d1",
            title="virt-manager deprecated in RHEL 9",
            chunk="virt-manager has been removed from RHEL 9.",
            documentKind="documentation",
        )
        text, sort_key = _format_portal_chunk(chunk)
        assert "Deprecation" in text
        assert sort_key <= 0

    def test_no_url_omits_line(self):
        """Chunk without URL omits the URL line."""
        chunk = PortalChunk(doc_id="d1", title="Test", chunk="Content.", documentKind="documentation")
        text, _ = _format_portal_chunk(chunk)
        assert "URL:" not in text

    def test_kind_labels_complete(self):
        """All expected document kinds have labels."""
        assert "documentation" in _KIND_LABELS
        assert "solution" in _KIND_LABELS
        assert "article" in _KIND_LABELS
        assert "Cve" in _KIND_LABELS
        assert "Erratum" in _KIND_LABELS


class TestFormatPortalResults:
    """Verify full result assembly with deprecation banner and budget."""

    def test_basic_formatting(self):
        """Multiple chunks are formatted and joined."""
        chunks = [
            PortalChunk(doc_id="a", title="Doc A", chunk="Content A.", documentKind="documentation"),
            PortalChunk(doc_id="b", title="Doc B", chunk="Content B.", documentKind="solution"),
        ]
        result = _format_portal_results(chunks, False, "test query", 10000)
        assert "Doc A" in result
        assert "Doc B" in result

    def test_deprecation_banner_when_flagged(self):
        """Deprecation warning banner is prepended when has_deprecation is True."""
        chunks = [PortalChunk(doc_id="a", title="Test", chunk="Normal content.", documentKind="documentation")]
        result = _format_portal_results(chunks, True, "test", 10000)
        assert result.startswith(_DEPRECATION_WARNING)

    def test_deprecation_banner_from_annotations(self):
        """Banner appears when chunk content triggers deprecation annotation."""
        chunks = [
            PortalChunk(
                doc_id="a",
                title="Removed Feature",
                chunk="This feature has been removed from RHEL 9.",
                documentKind="documentation",
            )
        ]
        result = _format_portal_results(chunks, False, "test", 10000)
        assert _DEPRECATION_WARNING in result

    def test_no_deprecation_banner_for_normal_content(self):
        """No deprecation banner when content is normal."""
        chunks = [PortalChunk(doc_id="a", title="Config Guide", chunk="Set up NFS.", documentKind="documentation")]
        result = _format_portal_results(chunks, False, "nfs", 10000)
        assert _DEPRECATION_WARNING not in result

    def test_budget_enforcement(self):
        """Results exceeding character budget are truncated."""
        chunks = [
            PortalChunk(doc_id=f"d{i}", title=f"Doc {i}", chunk="x" * 500, documentKind="documentation")
            for i in range(20)
        ]
        result = _format_portal_results(chunks, False, "test", 2000)
        assert len(result) <= 2500  # some overhead from truncation message
        assert "Doc 0" in result

    def test_empty_chunks_returns_no_results(self):
        """Empty chunk list returns 'no results' message."""
        result = _format_portal_results([], False, "missing query", 10000)
        assert "No results found" in result
        assert "missing query" in result


# ---------------------------------------------------------------------------
# Orchestrator (async, requires respx mocking)
# ---------------------------------------------------------------------------

_SOLR_ENDPOINT = "http://localhost:8983/solr/portal/select"


def _make_solr_json(docs, highlighting=None):
    """Build a Solr JSON response for respx mocking."""
    return {
        "responseHeader": {"status": 0, "QTime": 5},
        "response": {"numFound": len(docs), "docs": docs},
        "highlighting": highlighting or {},
    }


class TestRunPortalSearch:
    """Verify the async orchestrator pipeline."""

    async def test_returns_chunks_from_parallel_queries(self):
        """Orchestrator fires two queries and returns merged chunks."""
        doc_main = _make_doc(doc_id="main1", allTitle="Main Doc")
        doc_dep = _make_doc(doc_id="dep1", allTitle="Dep Doc")
        hl_main = {"main1": {"main_content": ["Main content snippet."]}}
        hl_dep = {"dep1": {"main_content": ["Deprecated feature removed."]}}

        call_count = 0
        with respx.mock(assert_all_called=False) as router:

            def side_effect(request):
                nonlocal call_count
                call_count += 1
                q = str(request.url.params.get("q", ""))
                if "deprecated" in q:
                    return httpx.Response(200, json=_make_solr_json([doc_dep], hl_dep))
                return httpx.Response(200, json=_make_solr_json([doc_main], hl_main))

            router.get(_SOLR_ENDPOINT).mock(side_effect=side_effect)

            async with httpx.AsyncClient() as client:
                chunks, _has_dep, _facets = await _run_portal_search(
                    "test query",
                    client=client,
                    solr_endpoint=_SOLR_ENDPOINT,
                    max_results=10,
                )

        assert len(chunks) >= 1
        assert call_count == 2  # main + deprecation queries fired

    async def test_respects_max_results(self):
        """Output is capped at max_results after deduplication."""
        docs = [_make_doc(doc_id=f"d{i}") for i in range(10)]
        hl = {f"d{i}": {"main_content": [f"Snippet {i}."]} for i in range(10)}

        with respx.mock(assert_all_called=False) as router:
            router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=_make_solr_json(docs, hl)))
            async with httpx.AsyncClient() as client:
                chunks, _, _facets = await _run_portal_search(
                    "test",
                    client=client,
                    solr_endpoint=_SOLR_ENDPOINT,
                    max_results=3,
                )

        assert len(chunks) <= 3

    async def test_empty_results(self):
        """Empty Solr response returns empty chunk list."""
        with respx.mock(assert_all_called=False) as router:
            router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=_make_solr_json([])))
            async with httpx.AsyncClient() as client:
                chunks, has_dep, _facets = await _run_portal_search(
                    "nonexistent",
                    client=client,
                    solr_endpoint=_SOLR_ENDPOINT,
                )

        assert chunks == []
        assert has_dep is False

    async def test_returns_facet_counts_from_main_query(self):
        """Orchestrator extracts facet counts from the main query response."""
        doc = _make_doc(doc_id="d1", kind="solution")
        hl = {"d1": {"main_content": ["Solution snippet."]}}
        solr_json = _make_solr_json([doc], hl)
        solr_json["facet_counts"] = {
            "facet_fields": {
                "documentKind": ["solution", 3, "Cve", 487, "documentation", 1],
            }
        }

        with respx.mock(assert_all_called=False) as router:

            def side_effect(request):
                """Return faceted response for main query, empty for deprecation."""
                q = str(request.url.params.get("q", ""))
                if "deprecated" in q:
                    return httpx.Response(200, json=_make_solr_json([]))
                return httpx.Response(200, json=solr_json)

            router.get(_SOLR_ENDPOINT).mock(side_effect=side_effect)

            async with httpx.AsyncClient() as client:
                _chunks, _has_dep, facets = await _run_portal_search(
                    "test query",
                    client=client,
                    solr_endpoint=_SOLR_ENDPOINT,
                )

        assert facets == {"documentKind": {"solution": 3, "Cve": 487, "documentation": 1}}

    async def test_empty_facets_when_solr_omits_facet_counts(self):
        """Facets are empty when Solr response has no facet_counts key."""
        with respx.mock(assert_all_called=False) as router:
            router.get(_SOLR_ENDPOINT).mock(return_value=httpx.Response(200, json=_make_solr_json([])))
            async with httpx.AsyncClient() as client:
                _chunks, _has_dep, facets = await _run_portal_search(
                    "test",
                    client=client,
                    solr_endpoint=_SOLR_ENDPOINT,
                )

        assert facets == {}


# ---------------------------------------------------------------------------
# Facet extraction
# ---------------------------------------------------------------------------


class TestExtractFacetCounts:
    """Verify Solr facet field extraction from response JSON."""

    def test_parses_alternating_list_format(self):
        """Converts Solr's [value, count, value, count, ...] into a dict."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", 487, "solution", 3, "documentation", 1],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert result == {"documentKind": {"Cve": 487, "solution": 3, "documentation": 1}}

    def test_drops_zero_count_entries(self):
        """Facet values with zero count are excluded."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", 10, "article", 0, "solution", 5],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert "article" not in result["documentKind"]
        assert result["documentKind"] == {"Cve": 10, "solution": 5}

    def test_multiple_facet_fields(self):
        """Multiple facet fields are extracted independently."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", 10],
                    "product": ["Red Hat Enterprise Linux", 42],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert "documentKind" in result
        assert "product" in result
        assert result["product"] == {"Red Hat Enterprise Linux": 42}

    def test_empty_facet_fields(self):
        """All-zero facet values produce an empty dict for that field."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", 0, "solution", 0],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert result == {}

    def test_missing_facet_counts_key(self):
        """Response without facet_counts returns empty dict."""
        result = _extract_facet_counts({"response": {"docs": []}})
        assert result == {}

    def test_missing_facet_fields_key(self):
        """facet_counts without facet_fields returns empty dict."""
        result = _extract_facet_counts({"facet_counts": {}})
        assert result == {}

    def test_odd_length_list_does_not_crash(self):
        """Malformed odd-length facet list is handled without IndexError."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", 10, "solution"],  # trailing orphan
                }
            }
        }
        result = _extract_facet_counts(response)
        # The trailing "solution" without a count is safely skipped
        assert result == {"documentKind": {"Cve": 10}}

    def test_non_dict_facet_counts_returns_empty(self):
        """Non-dict facet_counts value is treated as missing."""
        result = _extract_facet_counts({"facet_counts": "unexpected"})
        assert result == {}

    def test_non_dict_facet_fields_returns_empty(self):
        """Non-dict facet_fields value is treated as missing."""
        result = _extract_facet_counts({"facet_counts": {"facet_fields": "bad"}})
        assert result == {}

    def test_non_list_pairs_skipped(self):
        """Non-list facet pairs for a field are silently skipped."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": "not_a_list",
                    "product": ["RHEL", 10],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert "documentKind" not in result
        assert result == {"product": {"RHEL": 10}}

    def test_non_string_value_skipped(self):
        """Non-string facet values within pairs are skipped."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": [123, 10, "solution", 5],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert result == {"documentKind": {"solution": 5}}

    def test_non_int_count_skipped(self):
        """Non-integer count values within pairs are skipped."""
        response = {
            "facet_counts": {
                "facet_fields": {
                    "documentKind": ["Cve", "not_a_count", "solution", 5],
                }
            }
        }
        result = _extract_facet_counts(response)
        assert result == {"documentKind": {"solution": 5}}


# ---------------------------------------------------------------------------
# Facet summary formatting
# ---------------------------------------------------------------------------


class TestFormatFacetSummary:
    """Verify compact facet summary rendering for LLM output."""

    def test_basic_formatting(self):
        """Facet counts are rendered as 'count Label' sorted by descending count."""
        facets = {"documentKind": {"Cve": 487, "solution": 3, "documentation": 1}}
        result = _format_facet_summary(facets)
        assert result == "Result distribution: 487 CVE, 3 Solution, 1 Documentation"

    def test_single_facet(self):
        """Single document kind renders correctly."""
        facets = {"documentKind": {"solution": 42}}
        result = _format_facet_summary(facets)
        assert result == "Result distribution: 42 Solution"

    def test_uses_kind_facet_labels(self):
        """Facet labels are mapped through _KIND_FACET_LABELS."""
        facets = {"documentKind": {"Erratum": 15}}
        result = _format_facet_summary(facets)
        assert "15 Advisory" in result

    def test_unknown_kind_uses_raw_value(self):
        """Unknown document kinds fall through as-is."""
        facets = {"documentKind": {"mystery_type": 7}}
        result = _format_facet_summary(facets)
        assert "7 mystery_type" in result

    def test_empty_document_kind(self):
        """Empty documentKind facet returns empty string."""
        result = _format_facet_summary({"documentKind": {}})
        assert result == ""

    def test_no_document_kind_key(self):
        """Missing documentKind key returns empty string."""
        result = _format_facet_summary({"product": {"RHEL": 10}})
        assert result == ""

    def test_empty_facets(self):
        """Empty facets dict returns empty string."""
        result = _format_facet_summary({})
        assert result == ""

    def test_aliased_kinds_collapsed_into_single_label(self):
        """Aliased kinds that share a label are aggregated into one entry."""
        facets = {"documentKind": {"documentation": 5, "access-drupal10-node-type-page": 3, "Cve": 10}}
        result = _format_facet_summary(facets)
        assert result == "Result distribution: 10 CVE, 8 Documentation"

    def test_kind_facet_labels_cover_kind_labels(self):
        """_KIND_FACET_LABELS covers all keys in _KIND_LABELS for consistency."""
        for kind in _KIND_LABELS:
            assert kind in _KIND_FACET_LABELS, f"{kind!r} missing from _KIND_FACET_LABELS"


# ---------------------------------------------------------------------------
# Facet summary in formatted results
# ---------------------------------------------------------------------------


class TestFormatPortalResultsWithFacets:
    """Verify facet summary integration in _format_portal_results."""

    def test_facet_summary_prepended(self):
        """Facet summary appears before result content."""
        chunks = [PortalChunk(doc_id="a", title="Doc A", chunk="Content A.", documentKind="documentation")]
        facets = {"documentKind": {"documentation": 5, "Cve": 100}}
        result = _format_portal_results(chunks, False, "test", 10000, facets=facets)
        assert result.startswith("Result distribution:")
        assert "Doc A" in result

    def test_facet_before_deprecation_banner(self):
        """Facet summary appears before the deprecation warning banner."""
        chunks = [PortalChunk(doc_id="a", title="Test", chunk="Normal.", documentKind="documentation")]
        facets = {"documentKind": {"documentation": 1}}
        result = _format_portal_results(chunks, True, "test", 10000, facets=facets)
        facet_pos = result.index("Result distribution:")
        dep_pos = result.index("WARNING:")
        assert facet_pos < dep_pos

    def test_no_facets_no_summary(self):
        """No facet line when facets is None."""
        chunks = [PortalChunk(doc_id="a", title="Test", chunk="Content.", documentKind="documentation")]
        result = _format_portal_results(chunks, False, "test", 10000)
        assert "Result distribution:" not in result

    def test_empty_facets_no_summary(self):
        """No facet line when facets dict is empty."""
        chunks = [PortalChunk(doc_id="a", title="Test", chunk="Content.", documentKind="documentation")]
        result = _format_portal_results(chunks, False, "test", 10000, facets={})
        assert "Result distribution:" not in result
