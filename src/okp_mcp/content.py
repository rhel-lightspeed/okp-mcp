"""Content processing utilities for OKP MCP server."""

import re

_FAST_TRACK_PATTERN = re.compile(
    r"This solution is part of Red Hat's fast-track publication program.*",
    re.DOTALL,
)
_NOT_INCLUDED_PATTERN = re.compile(r"This content is not included\.")


def truncate_content(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, including the truncation message within the limit.

    Args:
        text: The text to truncate.
        max_chars: Maximum total characters in the returned string.

    Returns:
        Original text if under limit, otherwise a prefix plus truncation message
        that together do not exceed max_chars.
    """
    if len(text) <= max_chars:
        return text

    total_chars = len(text)
    # Reserve space for the suffix so the total output stays within max_chars.
    # Use a conservatively long placeholder to measure suffix length, then
    # recompute with the real kept count.
    suffix_template = f"\n\n[Content truncated - showing {{}} of {total_chars} characters]"
    # Longest possible kept count has same digits as max_chars
    suffix_max_len = len(suffix_template.format(max_chars))
    kept = max(0, max_chars - suffix_max_len)
    message = suffix_template.format(kept)
    return text[:kept] + message


def strip_boilerplate(text: str) -> str:
    """Remove known boilerplate patterns from text.

    Strips:
    - Fast-track publication program footer
    - "This content is not included." markers

    Args:
        text: The text to clean.

    Returns:
        Text with boilerplate patterns removed.
    """
    text = _FAST_TRACK_PATTERN.sub("", text)
    text = _NOT_INCLUDED_PATTERN.sub("", text)
    return text


def strip_index_suffix(path: str) -> str:
    """Remove trailing /index.html from URL paths.

    Solr document IDs include /index.html (e.g. /solutions/123/index.html)
    but access.redhat.com returns 404 for these paths.

    Args:
        path: URL path that may end with /index.html.

    Returns:
        Path with /index.html suffix removed.
    """
    return path.removesuffix("/index.html")


def clean_content(text: str | None, max_chars: int) -> str:
    """Clean and truncate content for LLM consumption.

    Chains strip_boilerplate then truncate_content.

    Args:
        text: The text to clean (None is handled gracefully).
        max_chars: Maximum characters to return.

    Returns:
        Cleaned and truncated text, or empty string if input is None.
    """
    if text is None:
        return ""

    text = strip_boilerplate(text)
    text = truncate_content(text, max_chars)
    return text
