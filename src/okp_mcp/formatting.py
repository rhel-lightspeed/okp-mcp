"""Result formatting, annotation, and sorting."""

import re

from .content import strip_boilerplate
from .solr import _extract_relevant_section, _get_highlights

EOL_PRODUCT_MENTIONS = [
    ("Red Hat Virtualization", "RHV"),
    ("RHEV", "RHV"),
    ("Red Hat Hyperconverged Infrastructure", "RHHI"),
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
    r"cockpit is the|virsh is the|vnc is the supported)\b",
    re.IGNORECASE,
)

SORT_REPLACEMENT = -1
SORT_DEPRECATION = 0
SORT_NORMAL = 1
SORT_EOL_PRODUCT = 2


def _determine_sort_key(has_replacement: bool, is_deprecated: bool, eol_product: str) -> int:
    """Determine result sort key with priority: deprecated > EOL product > replacement > normal.

    When multiple flags are true, last-writer-wins: SORT_DEPRECATION > SORT_EOL_PRODUCT > SORT_REPLACEMENT.
    If eol_product is set, always returns SORT_EOL_PRODUCT (overrides replacement flag).
    """
    sort_key = SORT_NORMAL
    if has_replacement and not eol_product:
        sort_key = SORT_REPLACEMENT
    if eol_product:
        sort_key = SORT_EOL_PRODUCT
    if is_deprecated and not eol_product:
        sort_key = SORT_DEPRECATION
    return sort_key


def _annotate_result(title: str, highlights: str, content: str) -> tuple[list[str], str, int]:
    """Scan title and content for deprecation, replacement, and EOL-product signals.

    Returns (annotations, applicability, sort_key) where sort_key controls
    result ordering: replacement first (-1), deprecation (0), normal (1),
    EOL-product last (2).
    """
    annotations: list[str] = []
    text = f"{title} {highlights} {content}"
    is_deprecated = bool(_DEPRECATION_RE.search(text))
    has_replacement = bool(_REPLACEMENT_RE.search(text))
    eol_product = ""
    for product_name, short in EOL_PRODUCT_MENTIONS:
        if product_name.lower() in text.lower():
            eol_product = f"{product_name} ({short})"
            break

    if has_replacement:
        annotations.append("\u2192 Recommended replacement mentioned")
    if is_deprecated:
        annotations.append("\u26a0\ufe0f Deprecation/Removal Notice")
    if eol_product:
        annotations.append(
            f"\u26a0\ufe0f RHV-only content below \u2014 not applicable to standard RHEL KVM (product: {eol_product})"
        )

    applicability = "RHEL"
    if eol_product:
        applicability = f"{eol_product} only"

    sort_key = _determine_sort_key(has_replacement, is_deprecated, eol_product)
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


async def _resolve_content_text(highlights: str, include_content: bool, doc: dict, query: str) -> str:
    """Resolve the content text to display: highlights take priority, then main_content."""
    if highlights:
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


async def _format_result(doc: dict, data: dict, include_content: bool = False, query: str = "") -> tuple[str, int]:
    """Format a single Solr document with applicability labels and annotations."""
    doc_id = doc.get("id", "")
    view_uri = doc.get("view_uri", "")
    title = doc.get("allTitle") or doc.get("heading_h1") or doc.get("title", "").split("|")[0].strip() or "Untitled"
    url_path = view_uri or doc_id
    highlights = _get_highlights(data, doc_id, view_uri, query=query)
    content_text = await _resolve_content_text(highlights, include_content, doc, query)

    annotations, applicability, sort_key = _annotate_result(title, highlights, content_text)
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
        result += f"\nContent: {content_text}"
    return result, sort_key
