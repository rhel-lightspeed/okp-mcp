"""Result formatting, annotation, and sorting."""

import re

from .content import doc_uri, strip_boilerplate
from .solr import _extract_relevant_section, _get_highlights

EOL_PRODUCT_MENTIONS = [
    ("Red Hat Virtualization", "RHV"),
    ("RHEV", "RHV"),
    ("Red Hat Hyperconverged Infrastructure", "RHHI"),
    ("Red Hat Fuse", "Fuse"),
    ("Red Hat Gluster Storage", "Gluster"),
]

_DEPRECATION_RE = re.compile(
    r"\b(deprecated|removed|no longer available|no longer supported|end of life|"
    r"has been removed|is not available|will be removed|not recommended|"
    r"maintenance support ended|extended life phase)\b",
    re.IGNORECASE,
)

_VERSION_LIST_RE = re.compile(
    r"(?:available for|planned for|is available for)[:\s]*"
    r"(?:RHEL[:\s]*)?"
    r"((?:\d+\.\d+\s*\([^)]+\)[,;\s]*(?:and\s*)?)+)",
    re.IGNORECASE,
)

_REPLACEMENT_RE = re.compile(
    r"\b(replaced by|use .{1,20} instead|the recommended replacement|"
    r"cockpit is the|virsh is the|vnc is the supported|"
    r"Enterprise Linux Release Dates|Enhanced .{0,30}EUS)\b",
    re.IGNORECASE,
)

SORT_REPLACEMENT = -1
SORT_DEPRECATION = 0
SORT_NORMAL = 1
SORT_EOL_SUBSTANTIVE = 1
SORT_EOL_PRODUCT = 2


def _is_substantive_eol_mention(query: str, product_name: str, title: str, content: str) -> bool:
    """Check if this is a substantive mention of an EOL product worthy of top ranking.

    Returns True if the user explicitly asked about the EOL product and the content
    provides substantial information about it, not just incidental mentions.
    """
    query_lower = query.lower()
    product_lower = product_name.lower()
    title_lower = title.lower()
    content_lower = content.lower()

    product_in_query = product_lower in query_lower

    product_aliases = {
        "red hat virtualization": ["rhv", "virtualization"],
        "red hat fuse": ["fuse", "integration"],
        "red hat gluster storage": ["gluster", "storage"],
    }

    aliases = product_aliases.get(product_lower, [])
    alias_in_query = any(alias in query_lower for alias in aliases)

    if not (product_in_query or alias_in_query):
        return False

    product_primary_in_title = product_lower in title_lower or any(alias in title_lower for alias in aliases)

    if not product_primary_in_title:
        return False

    migration_indicators = [
        "migrate",
        "migration",
        "replacement",
        "alternative",
        "superseded",
        "successor",
        "end of life",
        "eol",
    ]
    is_migration_content = any(indicator in content_lower for indicator in migration_indicators)

    configuration_indicators = [
        "configure",
        "configuration",
        "install",
        "setup",
        "deploy",
        "administration",
        "guide",
        "tutorial",
        "how to",
    ]
    is_configuration_content = any(indicator in content_lower for indicator in configuration_indicators)

    return is_migration_content or is_configuration_content


def _determine_sort_key(
    has_replacement: bool, is_deprecated: bool, eol_product: str, is_substantive: bool = False
) -> int:
    """Determine result sort key: deprecated > substantive EOL > EOL product > replacement > normal.

    If eol_product is set and is_substantive is True, returns SORT_EOL_SUBSTANTIVE (ranked with normal content).
    """
    sort_key = SORT_NORMAL
    if has_replacement and not eol_product:
        sort_key = SORT_REPLACEMENT
    if eol_product:
        sort_key = SORT_EOL_SUBSTANTIVE if is_substantive else SORT_EOL_PRODUCT
    if is_deprecated and not eol_product:
        sort_key = SORT_DEPRECATION
    return sort_key


def _scan_eol_product(text_lower: str, query: str, title: str, content: str, product: str) -> tuple[str, bool]:
    """Detect EOL product mentions, respecting the authoritative Solr product field.

    Returns (eol_product_label, is_substantive).  When *product* is set and
    does not match any EOL name the scan is skipped — the product field is
    the ground-truth for what a document is about.  When *product* itself IS
    an EOL product the substantive check is skipped (always demote).
    """
    if product and not any(pn.lower() in product.lower() for pn, _ in EOL_PRODUCT_MENTIONS):
        return "", False

    for product_name, short in EOL_PRODUCT_MENTIONS:
        if product_name.lower() not in text_lower:
            continue
        label = f"{product_name} ({short})"
        substantive = _is_substantive_eol_mention(query, product_name, title, content) if not product else False
        return label, substantive

    return "", False


def _annotate_result(
    title: str, highlights: str, content: str, query: str = "", product: str = ""
) -> tuple[list[str], str, int]:
    """Scan title and content for deprecation, replacement, and EOL-product signals.

    Returns (annotations, applicability, sort_key) where sort_key controls
    result ordering: replacement first (-1), deprecation (0), normal (1),
    EOL-product last (2).
    """
    annotations: list[str] = []
    text = f"{title} {highlights} {content}"
    is_deprecated = bool(_DEPRECATION_RE.search(text))
    has_replacement = bool(_REPLACEMENT_RE.search(text))

    eol_product, is_substantive = _scan_eol_product(text.lower(), query, title, content, product)

    if has_replacement:
        annotations.append("\u2192 Recommended replacement mentioned")
    if is_deprecated:
        annotations.append("\u26a0\ufe0f Deprecation/Removal Notice")
    if eol_product:
        if is_substantive:
            annotations.append(f"\u26a0\ufe0f EOL product content: {eol_product}")
        else:
            annotations.append(f"\u26a0\ufe0f EOL product \u2014 incidental mention (product: {eol_product})")

    applicability = "RHEL"
    if eol_product:
        applicability = f"{eol_product} only"

    sort_key = _determine_sort_key(has_replacement, is_deprecated, eol_product, is_substantive)
    return annotations, applicability, sort_key


def _extract_version_lists(text: str) -> str:
    """Pull out version lists (e.g. '9.0 (ended May 31, 2024)') as bullet points."""
    bullets: list[str] = []
    for match in _VERSION_LIST_RE.finditer(text):
        raw = match.group(1).strip().rstrip(",; ")
        entries = re.findall(r"(\d+\.\d+\s*\([^)]+\))", raw)
        for entry in entries:
            bullet = f"  - {entry.strip()}"
            if bullet not in bullets:
                bullets.append(bullet)
    return "\n".join(bullets)


_LARGE_DOC_THRESHOLD = 10_000

# Cap content per search result to prevent a single large document from
# consuming the entire tool response. With hl.fragsizeIsMinimum=true,
# Solr can produce highlight fragments of several thousand characters each,
# so even a few snippets can exceed the caller's budget and push all
# subsequent results out of the tool response.
_MAX_RESULT_CONTENT = 4_500


async def _resolve_content_text(highlights: str, include_content: bool, doc: dict, query: str) -> str:
    """Resolve the content text to display: highlights take priority, then main_content.

    For large documents (>10KB), Solr highlights may miss key overview paragraphs
    when earlier sections (e.g. tables of contents) consume highlight slots.
    In this case, BM25-extracted content supplements the highlights.
    """
    if highlights:
        if include_content and query and doc.get("main_content"):
            content = strip_boilerplate(doc["main_content"])
            if len(content) > _LARGE_DOC_THRESHOLD:
                bm25_section = _extract_relevant_section(content, query, per_section=1000, max_sections=1)
                if bm25_section:
                    return f"{highlights}\n\n---\n\n{bm25_section}"
        return highlights
    if include_content and doc.get("main_content"):
        content = strip_boilerplate(doc["main_content"])
        return _extract_relevant_section(content, query) if query else content[:2000]
    return ""


def _build_metadata_lines(doc: dict, kind_label: str | None, applicability: str, url_path: str) -> list[str]:
    """Build the metadata lines for a formatted search result.

    Returns a list of metadata strings (Type, Applicability, Product, URL, date)
    that will be joined with newlines in the result.
    """
    lines: list[str] = []
    if kind_label:
        lines.append(f"Type: {kind_label}")
    lines.append(f"Applicability: {applicability}")
    if doc.get("product"):
        product_line = f"Product: {doc['product']}"
        if doc.get("documentation_version"):
            product_line += f" {doc['documentation_version']}"
        lines.append(product_line)
    lines.append(f"URL: https://access.redhat.com{url_path}")
    if doc.get("lastModifiedDate"):
        date_str = doc["lastModifiedDate"][:10]
        lines.append(f"Last updated: {date_str}")
    return lines


async def _format_result(
    doc: dict, data: dict, include_content: bool = False, query: str = "", max_content: int = _MAX_RESULT_CONTENT
) -> tuple[str, int]:
    """Format a single Solr document with applicability labels and annotations."""
    doc_id = doc.get("id", "")
    view_uri = doc.get("view_uri", "")
    all_title = doc.get("allTitle")
    heading_h1 = doc.get("heading_h1")
    doc_title = doc.get("title", "")
    if all_title:
        title = all_title
    elif heading_h1:
        title = heading_h1[0] if isinstance(heading_h1, list) else heading_h1
    elif doc_title:
        title = doc_title.split("|")[0].strip()
    else:
        title = "Untitled"
    url_path = doc_uri(doc)
    highlights = _get_highlights(data, doc_id, view_uri, query=query)
    content_text = await _resolve_content_text(highlights, include_content, doc, query)

    annotations, applicability, sort_key = _annotate_result(
        title, highlights, content_text, query, product=doc.get("product", "")
    )
    doc_kind = doc.get("documentKind", "")
    kind_label = {"solution": "Solution", "article": "Article", "documentation": "Documentation"}.get(
        doc_kind, doc_kind.replace("access-drupal10-node-type-page", "Documentation")
    )

    result = ""
    if annotations:
        result += " | ".join(annotations) + "\n"
    result += f"**{title}**"
    result += "\n" + "\n".join(_build_metadata_lines(doc, kind_label, applicability, url_path))
    version_bullets = _extract_version_lists(content_text) if content_text else ""
    if version_bullets:
        result += f"\nReleases mentioned:\n{version_bullets}"
    if content_text:
        if len(content_text) > max_content:
            content_text = content_text[:max_content] + " [...]"
        result += f"\nContent: {content_text}"
    return result, sort_key
