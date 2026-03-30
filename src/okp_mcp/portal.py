"""Unified portal search: query builders, intent detection, and EOL product filtering."""

from __future__ import annotations

_EOL_PRODUCTS: frozenset[str] = frozenset(
    [
        "Red Hat Virtualization",
        "Red Hat Hyperconverged Infrastructure for Virtualization",
        "Red Hat JBoss Operations Network",
        "Red Hat Fuse",
        "Red Hat Single Sign-On",
        "Red Hat Single Sign-On Continuous Delivery",
        "Red Hat CodeReady Workspaces",
        "Red Hat CodeReady Studio",
        "Red Hat JBoss Data Virtualization",
        "Red Hat Container Development Kit",
        "Red Hat Gluster Storage",
        "Red Hat JBoss Developer Studio",
        "Red Hat JBoss Developer Studio Integration Stack",
        "Red Hat Application Migration Toolkit",
        "Red Hat Software Collections",
        "JBoss Enterprise SOA Platform",
        "JBoss Enterprise Application Platform Continuous Delivery",
        "Red Hat Development Suite",
        "Red Hat Developer Toolset",
        "OpenShift Online",
        "Red Hat JBoss Fuse Service Works",
        "Red Hat Certificate System",
        "Red Hat Process Automation Manager",
        "Red Hat Decision Manager",
        "Red Hat OpenShift Container Storage",
    ]
)

# Highlight term expansions injected into hl.q for intent-specific queries.
_VM_HIGHLIGHT_TERMS = "virsh cockpit deprecated virt-manager"
_EUS_HIGHLIGHT_TERMS = '"Enhanced EUS" "48 months" "Enhanced Extended Update Support"'


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


_VM_INTENT_RE = re.compile(r"\b(?:vm|vms|virtual machine|virtualization|hypervisor)\b")
_RELEASE_DATE_INTENT_RE = re.compile(r"\b(?:release dates?|released|when was|general availability)\b")
_EUS_INTENT_RE = re.compile(r"\b(?:eus|extended update support)\b")


def _detect_vm_intent(query_lower: str) -> bool:
    """Return True if the lowercased query contains VM/virtualization keywords."""
    return bool(_VM_INTENT_RE.search(query_lower))


def _detect_release_date_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about release dates or when something was released."""
    return bool(_RELEASE_DATE_INTENT_RE.search(query_lower))


def _detect_eus_intent(query_lower: str) -> bool:
    """Return True if the lowercased query asks about EUS or Extended Update Support."""
    return bool(_EUS_INTENT_RE.search(query_lower))


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------

# Fields returned by the main (all-type) query.  Includes type-specific fields
# (cve_details, portal_synopsis, ...) so the chunk-conversion layer (Phase 2)
# can fall back to them when highlighting returns no snippets.
_MAIN_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,"
    "product,documentation_version,lastModifiedDate,score,"
    "cve_details,cve_threatSeverity,"
    "portal_synopsis,portal_summary,portal_severity,portal_advisory_type"
)

# Fields returned by the deprecation query (no CVE/errata-specific fields).
_DEPRECATION_FL = (
    "id,allTitle,heading_h1,title,view_uri,url_slug,documentKind,product,documentation_version,lastModifiedDate,score"
)

# Custom qf that adds errata-specific field boosts on top of the base edismax
# weights defined in solr._solr_query().  cve_details is a Solr `string` field
# (not tokenized) so it cannot participate in edismax scoring; CVEs match via
# allTitle, main_content, and all_content instead.
_MAIN_QF = "title^5 main_content heading_h1^3 heading_h2 portal_synopsis^3 allTitle^3 content^2 all_content^1"


def _build_eol_filter() -> str:
    """Build a Solr fq clause that excludes all EOL products."""
    return " AND ".join(f'-product:"{p}"' for p in _EOL_PRODUCTS)


def _build_main_query(cleaned_query: str) -> dict:
    """Build Solr params for the unified main query (all document types).

    The returned dict is passed to ``_solr_query()`` which merges it into its
    base edismax params.  Keys here override the base defaults (e.g. ``qf``,
    ``hl.defaultSummary``).

    Intent-specific boosts (VM, EUS, release-date) are applied by
    ``_apply_intent_boosts`` after construction.
    """
    return {
        "q": cleaned_query,
        "fq": _build_eol_filter(),
        "qf": _MAIN_QF,
        "fl": _MAIN_FL,
        "rows": 20,
        "hl.snippets": "6",
        # Override the base default of "true".  CVE/errata main_content starts
        # with boilerplate; disabling defaultSummary forces the chunk layer to
        # fall back to type-specific fields (cve_details, portal_synopsis).
        "hl.defaultSummary": "false",
        # Slight recency boost so newer RHEL content ranks higher.
        "bf": "recip(ms(NOW,lastModifiedDate),3.16e-11,1,1)^0.3",
    }


def _build_deprecation_query(cleaned_query: str) -> dict:
    """Build Solr params for the deprecation-focused query.

    Appends "deprecated removed" to the user query and heavily boosts
    documents whose titles or content mention deprecation/removal.  Restricted
    to docs/solutions/articles (CVEs/errata don't carry deprecation notices).
    """
    eol_fq = _build_eol_filter()
    return {
        "q": f"{cleaned_query} deprecated removed",
        "fq": [
            "documentKind:(solution OR article OR documentation)",
            eol_fq,
        ],
        "fl": _DEPRECATION_FL,
        "rows": 5,
        "hl.snippets": "4",
        "bq": (
            'allTitle:(deprecated OR removed OR "no longer" OR "end of life")^20 '
            'allTitle:("release notes" OR "considerations in adopting")^15 '
            'main_content:(deprecated OR removed OR "no longer available")^10'
        ),
    }


def _apply_intent_boosts(params: dict, query_lower: str, cleaned_query: str) -> None:
    """Mutate *params* in-place to add intent-specific bq/hl.q boosts.

    Called after ``_build_main_query`` to layer on VM, EUS, or release-date
    boosts without complicating the base query builder.
    """
    if _detect_vm_intent(query_lower):
        params["bq"] = (
            'title:(cockpit OR virtualization OR "virt-manager")^15 main_content:(cockpit OR "cockpit-machines")^5'
        )
        params["hl.q"] = f"{cleaned_query} {_VM_HIGHLIGHT_TERMS}"

    if _detect_eus_intent(query_lower):
        params["bq"] = 'title:"Enhanced EUS"^100 title:"EUS FAQ"^80'
        params["hl.q"] = f"{cleaned_query} {_EUS_HIGHLIGHT_TERMS}"

    if _detect_release_date_intent(query_lower):
        params["bq"] = 'title:"Enterprise Linux Release Dates"^200 allTitle:"release dates"^30'
