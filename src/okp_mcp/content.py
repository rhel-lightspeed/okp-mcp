"""Content processing utilities for OKP MCP server."""

import re

_FAST_TRACK_PATTERN = re.compile(
    r"This solution is part of Red Hat's fast-track publication program.*",
    re.DOTALL,
)
_NOT_INCLUDED_PATTERN = re.compile(r"This content is not included\.")


def truncate_content(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending truncation message if needed.

    Args:
        text: The text to truncate.
        max_chars: Maximum number of characters to keep.

    Returns:
        Original text if under limit, otherwise first max_chars plus truncation message.
    """
    if len(text) <= max_chars:
        return text

    total_chars = len(text)
    truncated = text[:max_chars]
    message = f"\n\n[Content truncated - showing {max_chars} of {total_chars} characters]"
    return truncated + message


def _select_within_budget(results: list[str], max_chars: int, query: str) -> str:
    """Select results within character budget, dropping lower-priority tail results.

    Iterates through pre-sorted results, accumulating them until adding the next
    would exceed max_chars. Appends a truncation message when results are dropped.
    A single result that exceeds the budget is hard-truncated via truncate_content.

    Args:
        results: Pre-formatted, priority-sorted result strings to include.
        max_chars: Maximum total characters in the output.
        query: Original search query (used in fallback messages).

    Returns:
        Joined result string within the character budget, with truncation notice if needed.
    """
    if not results:
        return f"No results found for: {query}"

    if len(results) == 1:
        if len(results[0]) > max_chars:
            return truncate_content(results[0], max_chars)
        return results[0]

    separator = "\n\n---\n\n"
    included = []
    chars_used = 0

    for result in results:
        result_len = len(result)
        if included:
            result_len += len(separator)

        if chars_used + result_len > max_chars:
            break

        included.append(result)
        chars_used += result_len

    if not included:
        return truncate_content(results[0], max_chars)

    output = separator.join(included)

    if len(included) < len(results):
        message = (
            f"\n\n[Budget reached - showing {len(included)} of {len(results)} "
            f"results ({chars_used:,} of {max_chars:,} chars)]"
        )
        output += message

    return output


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


def doc_uri(doc: dict) -> str:
    """Return the canonical URL path for a Solr document.

    Prefers view_uri, falls back to id. Strips trailing /index.html
    because Solr document IDs carry it but access.redhat.com 404s on those paths.
    """
    uri = doc.get("view_uri") or doc.get("id", "")
    return uri.removesuffix("/index.html")


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
