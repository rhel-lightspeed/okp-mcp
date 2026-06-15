"""Result annotation and sorting."""

import re


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

_REPLACEMENT_RE = re.compile(
    r"\b(replaced by|use .{1,20} instead|the recommended replacement|"
    r"cockpit is the|virsh is the|vnc is the supported|"
    r"Enterprise Linux Release Dates|Enhanced .{0,30}EUS)\b",
    re.IGNORECASE,
)

SORT_REPLACEMENT = -1
SORT_DEPRECATION = 0
SORT_NORMAL = 1
SORT_EOL_PRODUCT = 2


def _determine_sort_key(has_replacement: bool, is_deprecated: bool, eol_product: str) -> int:
    """Determine result sort key: replacement first (-1), deprecation (0), normal (1), EOL last (2)."""
    sort_key = SORT_NORMAL
    if has_replacement and not eol_product:
        sort_key = SORT_REPLACEMENT
    if eol_product:
        sort_key = SORT_EOL_PRODUCT
    if is_deprecated and not eol_product:
        sort_key = SORT_DEPRECATION
    return sort_key


def _scan_eol_product(text_lower: str, product: str) -> str:
    """Detect EOL product mentions, respecting the authoritative Solr product field.

    Returns the EOL product label (e.g. "Red Hat Virtualization (RHV)") or empty string.
    When *product* is set and does not match any EOL name the scan is skipped —
    the product field is the ground-truth for what a document is about.
    """
    if product and not any(pn.lower() in product.lower() for pn, _ in EOL_PRODUCT_MENTIONS):
        return ""

    for product_name, short in EOL_PRODUCT_MENTIONS:
        if product_name.lower() in text_lower:
            return f"{product_name} ({short})"

    return ""


def annotate_result(title: str, highlights: str, content: str, product: str = "") -> tuple[list[str], str, int]:
    """Scan title and content for deprecation, replacement, and EOL-product signals.

    Returns (annotations, applicability, sort_key) where sort_key controls
    result ordering: replacement first (-1), deprecation (0), normal (1),
    EOL-product last (2).
    """
    annotations: list[str] = []
    text = f"{title} {highlights} {content}"
    is_deprecated = bool(_DEPRECATION_RE.search(text))
    has_replacement = bool(_REPLACEMENT_RE.search(text))

    eol_product = _scan_eol_product(text.lower(), product)

    if has_replacement:
        annotations.append("\u2192 Recommended replacement mentioned")
    if is_deprecated:
        annotations.append("\u26a0\ufe0f Deprecation/Removal Notice")
    if eol_product:
        annotations.append(f"\u26a0\ufe0f EOL product \u2014 incidental mention (product: {eol_product})")

    applicability = "RHEL"
    if eol_product:
        applicability = f"{eol_product} only"

    sort_key = _determine_sort_key(has_replacement, is_deprecated, eol_product)
    return annotations, applicability, sort_key
