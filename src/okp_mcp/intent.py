"""Intent detection and Solr boost application for portal search queries."""

# Each intent maps a regex pattern to Solr boost queries (bq) and highlight
# terms (hl.q) that improve search relevance for specific topics.  Intents
# are evaluated first-match-wins: the registry is ordered from most specific
# to most generic, and the first matching intent's boosts are applied.
#
# To add a new intent, append an IntentRule to INTENT_RULES at the correct
# priority position (more specific intents before more generic ones) and add
# a functional test to verify.

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import logger

# ---------------------------------------------------------------------------
# Intent rule dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntentRule:
    """A single intent detection rule with Solr boost configuration.

    Each rule pairs a regex pattern with the Solr query parameters to inject
    when the pattern matches.  Rules with both main-query and deprecation-query
    boosts participate in both search pipelines.

    Attributes:
        name: Short identifier for logging and testing.
        pattern: Regex pattern matched against the lowercased user query.
        bq: Solr boost query for the main search (replaces any existing bq).
        highlight_terms: Extra terms appended to hl.q for snippet selection.
        dep_title_terms: Solr OR-joined terms for the deprecation allTitle boost.
        dep_content_terms: Solr OR-joined terms for the deprecation main_content boost.
    """

    name: str
    pattern: str
    bq: str = ""
    highlight_terms: str = ""
    dep_title_terms: str = ""
    dep_content_terms: str = ""

    def __post_init__(self) -> None:
        """Validate that deprecation boost fields are either both set or both empty."""
        if bool(self.dep_title_terms) != bool(self.dep_content_terms):
            msg = f"IntentRule {self.name!r}: dep_title_terms and dep_content_terms must both be set or both be empty"
            raise ValueError(msg)

    def matches(self, query: str) -> bool:
        """Return True if the query matches this intent's pattern (case-insensitive)."""
        return bool(re.search(self.pattern, query, re.IGNORECASE))


# ---------------------------------------------------------------------------
# Intent registry
# ---------------------------------------------------------------------------

# Ordered by priority: most specific first, most generic last.
# First match wins for both main-query and deprecation-query boosts.
#
# Priority rationale:
#   - release_date: very specific query type, no overlap with other intents.
#   - eus: specific product lifecycle concept, rarely co-occurs with VM/SPICE.
#   - lifecycle: broader than EUS but still specific to support duration/phase
#     queries.  Must come after EUS so "EUS lifecycle" matches EUS first.
#     See functional test RSPEED_2698.
#   - spice: SPICE queries contain VM terms ("VMs") but need display-protocol
#     boosts (VNC), not VM management boosts (cockpit/virsh).  Must match
#     before the generic VM intent.  See functional test RSPEED_2481.
#   - cloud_deploy: AWS/EC2 queries need deployment guide boosts.  Must come
#     before VM so "RHEL VM on AWS" gets cloud boosts, not cockpit/virsh.
#     See functional test RSPEED_2201.
#   - sap: SAP queries need system-roles/preconfigure boosts.  Must come
#     before VM so "SAP HANA VM" gets SAP boosts.  See functional test
#     sap_004.
#   - container_compat: container + RHEL queries need container support policy
#     and compatibility matrix boosts.  See functional test RSPEED_2482.
#   - programming_language: Python/Ruby/etc. queries need Application Streams
#     boosts.  See functional test RSPEED_2294.
#   - gfs2: GFS2/Resilient Storage queries need removal/discontinuation boosts.
#     Without them, "Is GFS2 available in RHEL 10?" returns generic "Is X
#     available?" solutions.  See functional test RSPEED_2794.
#   - ethtool: NIC driver debugging queries need msglvl highlight terms.
#     See functional test RSPEED_2123.
#   - vm: broadest intent, catches all VM/virtualization queries that didn't
#     match a more specific intent above.
INTENT_RULES: list[IntentRule] = [
    IntentRule(
        name="release_date",
        pattern=r"\b(?:release dates?|released|when was|general availability)\b",
        bq='title:"Enterprise Linux Release Dates"^200 allTitle:"release dates"^30',
    ),
    IntentRule(
        name="eus",
        pattern=r"\b(?:eus|extended update support)\b",
        bq='title:"Enhanced EUS"^100 title:"EUS FAQ"^80',
        highlight_terms='"Enhanced EUS" "48 months" "Enhanced Extended Update Support"',
    ),
    # Lifecycle queries ask about support duration, phases, or EOL dates for
    # a RHEL major release.  Without this, "RHEL 10 support lifecycle" pulls
    # in random RHEL 10 solutions and the deprecation side-query surfaces
    # unrelated deprecation notices (e.g. BIND on RHEL 10), causing the LLM
    # to answer from training data instead of official lifecycle docs.
    # Must come after EUS (EUS is a specific lifecycle sub-topic).
    IntentRule(
        name="lifecycle",
        pattern=r"\blife[\s-]?cycles?\b",
        bq=(
            'allTitle:("Life Cycle" OR "life-cycle" OR "lifecycle")^100 '
            'allTitle:("updates" OR "errata" OR "support policy")^30'
        ),
        highlight_terms='"Life Cycle" "full support" "maintenance support" "extended life"',
    ),
    # SPICE is a display/graphics protocol for VMs, NOT a VM management tool.
    # Queries mentioning SPICE need display-protocol-specific boosts (VNC),
    # not VM management boosts (cockpit/virsh).  Without this, queries like
    # "Is SPICE available for VMs?" trigger VM intent, which injects
    # cockpit/virsh highlight terms that flood results with irrelevant
    # VM management content and push out the critical VNC replacement
    # information the LLM needs to answer correctly.
    IntentRule(
        name="spice",
        pattern=r"\bspice\b",
        bq=(
            'allTitle:(spice OR deprecated OR "no longer")^15 '
            'main_content:(VNC OR deprecated OR removed OR replacement OR "no longer")^10'
        ),
        highlight_terms="VNC deprecated removed replacement",
        dep_title_terms='SPICE OR VNC OR "display protocol"',
        dep_content_terms='SPICE OR VNC OR "display protocol"',
    ),
    # Cloud deployment queries (AWS, EC2) need deployment guide boosts, not
    # generic solution articles.  Without this, "RHEL AWS Secure Boot" pulls
    # in kernel module signing solutions instead of the deploying_rhel_on_aws
    # documentation that covers marketplace images and custom AMIs.
    # Must come before VM so "RHEL VM on AWS" gets cloud boosts, not
    # cockpit/virsh boosts.
    IntentRule(
        name="cloud_deploy",
        pattern=r"\b(?:aws|amazon\s+web\s+services|ec2)\b",
        bq=(
            'allTitle:(deploying OR "amazon web services" OR AWS)^30 '
            'main_content:(marketplace OR AMI OR "custom image")^10'
        ),
        highlight_terms='deploying marketplace "custom image" AMI AWS',
    ),
    # SAP queries need boosts for SAP system roles and preconfigure
    # documentation.  Without this, "RHEL System Roles for SAP" (cleaned
    # to "RHEL Roles SAP" after stopword removal of "System") returns
    # generic SAP docs without the three preconfigure role names in
    # snippets, causing the LLM to make 12+ search calls.  The
    # highlight_terms inject preconfigure role names into hl.q so Solr
    # selects passages containing them.  Must come before VM so "SAP
    # HANA VM" gets SAP boosts.
    IntentRule(
        name="sap",
        pattern=r"\bsap\b",
        bq=(
            'allTitle:("system roles" OR "SAP solutions" OR SAP OR preconfigure)^30 '
            "main_content:(sap_general_preconfigure OR sap_netweaver_preconfigure OR sap_hana_preconfigure)^15"
        ),
        highlight_terms=(
            "sap_general_preconfigure sap_netweaver_preconfigure sap_hana_preconfigure "
            '"system roles" preconfigure "SAP Application Server"'
        ),
    ),
    # Container compatibility/support queries need boosts for the container
    # support policy (article 2726611) and the RHEL container compatibility
    # matrix.  Without this, "Can I run a RHEL 6 container on RHEL 9?"
    # returns unrelated deprecation docs because the cleaned query "RHEL 6
    # container RHEL 9" is too generic.  See functional test RSPEED_2482.
    IntentRule(
        name="container_compat",
        pattern=r"\bcontainers?\b.*\brhel\b|\brhel\b.*\bcontainers?\b",
        bq=(
            'allTitle:("container support" OR "container compatibility" OR "compatibility matrix")^100 '
            'title:("support policy" OR compatibility)^50 '
            "main_content:(container AND host AND supported)^20"
        ),
        highlight_terms='"container support" "compatibility matrix" supported unsupported',
    ),
    # Programming language queries (Python, Ruby, etc.) need boosts for the
    # Application Streams / dynamic programming languages documentation.
    # Without this, "What is most current version of Python for RHEL 10?"
    # gets drowned by JBoss EAP deprecation docs from the side query because
    # "10" matches Java/JakartaEE content.  See functional test RSPEED_2294.
    IntentRule(
        name="programming_language",
        pattern=r"\b(?:python|perl|ruby|php)\b",
        bq=(
            'allTitle:("dynamic programming languages" OR "Application Stream" OR "Application Streams")^50 '
            'title:("installing and using" OR "programming languages")^30 '
            'main_content:("Application Stream" OR "programming languages")^10'
        ),
        highlight_terms='"Application Stream" "programming languages" version',
    ),
    # GFS2 / Resilient Storage queries need boosts for removal/discontinuation
    # docs.  Without this, "Is GFS2 available in RHEL 10?" returns generic
    # "Is X available in RHEL 10?" solutions because "available" + "RHEL" +
    # "10" match many solution titles strongly, drowning out the specific
    # GFS2 removal content from article 7092011 and the "Considerations in
    # adopting RHEL 10" documentation.  See functional test RSPEED_2794.
    IntentRule(
        name="gfs2",
        pattern=r"\b(?:gfs2|resilient\s+storage)\b",
        bq=(
            'allTitle:(GFS2 OR "Resilient Storage" OR discontinued OR removed)^30 '
            'main_content:(GFS2 OR "Resilient Storage" OR discontinued OR removed OR "no longer")^15'
        ),
        highlight_terms='GFS2 "Resilient Storage" removed discontinued "no longer supported"',
        dep_title_terms='GFS2 OR "Resilient Storage" OR "file system"',
        dep_content_terms='GFS2 OR "Resilient Storage" OR "file system" OR discontinued',
    ),
    # ethtool / NIC driver queries need msglvl and message-level terms
    # injected into hl.q so Solr picks passages containing the actual
    # debugging commands.  Without this, solution 45950 (bnxt_en debugging)
    # returns a short default-summary snippet that stops before the msglvl
    # commands, and get_document fails due to Solr ID mismatch, so the LLM
    # never sees the answer.  See functional test RSPEED_2123.
    IntentRule(
        name="ethtool",
        pattern=r"\b(?:ethtool|bnxt[\w-]*|nic\s+driver|network\s+driver)\b",
        bq='main_content:(msglvl OR ethtool OR "message level")^10',
        highlight_terms='msglvl ethtool "message level" "ethtool -s"',
    ),
    IntentRule(
        name="vm",
        pattern=r"\b(?:vm|vms|virtual machines?|virtualization|hypervisors?)\b",
        bq=(
            'title:(cockpit OR virtualization OR "virt-manager")^15 '
            'main_content:(cockpit OR "cockpit-machines" OR virsh)^5'
        ),
        highlight_terms="virsh cockpit deprecated virt-manager",
        dep_title_terms='"virt-manager" OR virtualization OR cockpit OR "virtual machine"',
        dep_content_terms='"virt-manager" OR cockpit OR virsh OR "virtual machine"',
    ),
]


# ---------------------------------------------------------------------------
# Boost application
# ---------------------------------------------------------------------------

# Boost weights for deprecation intent terms.
#
# IMPORTANT: Keep these at ^5/^3 or lower.  Higher values (e.g. ^30/^15)
# inflate deprecation query scores well above main query scores (~120),
# causing _filter_by_score to drop ALL main results because they fall
# below 45% of the inflated top score.  The score filter uses raw Solr
# scores which are NOT comparable across queries with different bq boosts.
#
# At ^5/^3, the intent boosts are strong enough to re-rank within the
# deprecation results (e.g. VM deprecation > Vert.x deprecation) without
# overwhelming the cross-query score comparison.
_DEP_TITLE_BOOST = 5
_DEP_CONTENT_BOOST = 3


def apply_main_boosts(params: dict, query_lower: str, cleaned_query: str) -> None:
    """Mutate *params* in-place to add intent-specific bq/hl.q boosts.

    Called after ``_build_main_query`` to layer on intent-specific boosts
    without complicating the base query builder.

    First matching intent wins: the registry is ordered from most specific
    to most generic, so SPICE matches before VM, EUS before VM, etc.
    """
    for rule in INTENT_RULES:
        if rule.matches(query_lower) and rule.bq:
            params["bq"] = rule.bq
            if rule.highlight_terms:
                params["hl.q"] = f"{cleaned_query} {rule.highlight_terms}"
            boosted = "bq + hl.q" if rule.highlight_terms else "bq"
            logger.info("Intent boost: applied '%s' to main query (%s)", rule.name, boosted)
            return


def apply_deprecation_boosts(params: dict, query_lower: str) -> None:
    """Add topic-specific boosts to the deprecation query based on detected intent.

    Without intent-aware boosts, the deprecation query matches ANY content
    containing "deprecated" and "removed", pulling in noise results like
    Eclipse Vert.x release notes or network teaming deprecation notices for
    a VM management question.  Adding intent-specific bq ensures the
    deprecation query surfaces deprecation notices about the user's actual
    topic, not unrelated deprecation content.

    This function APPENDS to the existing bq (the base deprecation term
    boosts defined in _build_deprecation_query) rather than replacing it,
    so the original deprecation/removal title boosts remain active.

    First matching intent wins (evaluated in INTENT_RULES order).
    Queries that match no intent are left unchanged.

    See functional tests RSPEED_2480 (VM management) and RSPEED_2481 (SPICE).
    """
    for rule in INTENT_RULES:
        if not rule.matches(query_lower):
            continue
        # First match wins: if the matched rule has no deprecation terms,
        # stop searching rather than falling through to a less-specific
        # rule.  E.g., "when was virtualization released" matches
        # release_date first (no dep terms) and must not fall through
        # to VM deprecation boosts.
        if not rule.dep_title_terms:
            return
        existing_bq = params.get("bq", "")
        params["bq"] = (
            f"{existing_bq} "
            f"allTitle:({rule.dep_title_terms})^{_DEP_TITLE_BOOST} "
            f"main_content:({rule.dep_content_terms})^{_DEP_CONTENT_BOOST}"
        )
        logger.info(
            "Intent boost: applied '%s' to deprecation query (^%d/^%d)", rule.name, _DEP_TITLE_BOOST, _DEP_CONTENT_BOOST
        )
        return
