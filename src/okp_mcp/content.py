"""Content processing utilities for OKP MCP server."""

import re

from collections.abc import Iterable
from collections.abc import Sequence

from okp_mcp.outline import Section
from okp_mcp.types import SolrDoc


# Character budget for the outline format_sections renders. Sampled over the
# HTML mirror, a page's full outline runs to ~13KB at the p90 and 37KB at the
# worst case (460 sections), against a 30K default response budget -- so the
# large tail has to be trimmed. The budget is on characters rather than entry
# count because entry counts vary by two orders of magnitude (median 22, p90
# 181) and a count that suits a guide starves a reference manual.
_MAX_OUTLINE_CHARS = 15_000

# Solr content uses a Unicode right single quotation mark (U+2019, a.k.a.
# "smart apostrophe") in "Red Hat\u2019s", not the ASCII apostrophe (U+0027).
# The character class ['\u2019] matches either variant so the pattern works
# regardless of which encoding the Solr index or content pipeline produces.
_FAST_TRACK_PATTERN = re.compile(
    r"This solution is part of Red Hat['\u2019]s fast-track publication program.*",
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


def clean_heading(title: str) -> str:
    """Normalise a Solr heading for display.

    Solr stores documentation headings with non-breaking spaces around the
    numbering, e.g. ``"Chapter\\u00a09.\\u00a0Admission plugins"``; 91% of the
    indexed documentation pages carry at least one.  Left as-is they render as
    an odd glyph and break naive whitespace matching, so collapse every run of
    whitespace (NBSP included) into a single ASCII space.
    """
    return re.sub(r"\s+", " ", title).strip()


def _dedup_headings(titles: Iterable[str], seen: set[str]) -> list[str]:
    """Clean, drop empties, and drop repeats, preserving document order.

    ``seen`` is updated in place so a title that appears as both an h1 and an
    h2 is listed once, under the first level it occurs in.
    """
    kept: list[str] = []
    for title in titles:
        cleaned = clean_heading(title)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        kept.append(cleaned)
    return kept


def _outline_width(lines: list[str]) -> int:
    """Rendered length of outline lines, counting the indent and newline."""
    return sum(len(line) + 3 for line in lines)


def _shed_depth(entries: list[tuple[int, str]], max_chars: int) -> tuple[list[str], bool]:
    """Drop the deepest nesting levels until the outline fits the budget.

    Returns the surviving lines and whether any level was dropped.
    """
    full_depth = max((level for level, _ in entries), default=1)
    depth = full_depth
    kept = [line for level, line in entries if level <= depth]

    while _outline_width(kept) > max_chars and depth > 1:
        depth -= 1
        kept = [line for level, line in entries if level <= depth]

    return kept, depth < full_depth


def _trim_note(kept: int, total: int, shed: bool, truncated: bool) -> str:
    """Describe what the budget left out, or nothing when the outline is whole."""
    reasons = []
    if shed:
        reasons.append("deeper subsections omitted")
    if truncated:
        reasons.append("list truncated")
    if not reasons:
        return ""
    return f"\n  [showing {kept} of {total} sections, {' and '.join(reasons)}]"


def _fit_to_budget(entries: list[tuple[int, str]], max_chars: int) -> tuple[list[str], str]:
    """Trim an outline to ``max_chars``, shedding the deepest levels first.

    A flat "first N entries" cap truncates the tail, which is where the later
    chapters live -- exactly the sections a reader scrolls to. Dropping whole
    nesting levels instead keeps the document's shape: a 460-section reference
    degrades to its top levels spanning the whole guide rather than to its
    first 60 subsections.

    Returns the lines to render and a note describing what was left out.
    """
    kept, shed = _shed_depth(entries, max_chars)

    # The top level alone can still overflow -- a reference manual whose every
    # entry is a chapter -- so count truncation backstops the depth shedding.
    truncated = False
    while kept and _outline_width(kept) > max_chars:
        kept.pop()
        truncated = True

    return kept, _trim_note(len(kept), len(entries), shed, truncated)


def _format_outline(entries: list[tuple[int, str]], url: str, linkable: bool, max_chars: int) -> str:
    """Render an outline block, noting what was trimmed and how to cite a section."""
    lines, note = _fit_to_budget(entries, max_chars)
    if not lines:
        return ""

    body = "\n".join(f"  {line}" for line in lines)
    result = f"\n\nSections in this document:\n{body}{note}"

    if linkable:
        guidance = f"Link to a section by appending its fragment to the URL, e.g. {url}{lines[0].split(' ', 1)[0]}"
    else:
        guidance = (
            "Cite a section by the title above. This page has no per-section "
            "anchors — link to the document URL and do not invent a #fragment."
        )
    return f"{result}\n{guidance}"


def format_sections(
    doc: SolrDoc,
    sections: Sequence[Section] = (),
    url: str = "",
    max_chars: int = _MAX_OUTLINE_CHARS,
) -> str:
    """Return the section outline of a documentation page.

    When ``sections`` carries anchors read from the HTML mirror, each entry is
    rendered as a ``#fragment`` plus its title, so the caller can build a link
    that resolves on docs.redhat.com.  Anchors cannot come from Solr: they are
    author-assigned AsciiDoc IDs ("Kafka tuning overview" lives at
    ``#con-config-tuning-intro-str``) and no indexed field carries them.

    Falls back to listing ``heading_h1`` then ``heading_h2`` by title alone
    when no anchors are available -- an unreachable mirror, or one of the
    single-topic pages that has no subsections. Returns an empty string when
    the document has no headings at all (solutions, errata, CVEs).
    """
    if sections:
        entries = [(section.level, f"#{section.anchor} — {section.title}") for section in sections]
        return _format_outline(entries, url, linkable=True, max_chars=max_chars)

    seen: set[str] = set()
    chapters = _dedup_headings(doc.heading_h1, seen)
    headings = _dedup_headings(doc.heading_h2, seen)
    if not chapters and not headings:
        return ""

    # h1 is the chapter level and h2 the section level, so the same depth
    # shedding applies to the Solr fallback.
    entries = [(1, title) for title in chapters] + [(2, title) for title in headings]
    return _format_outline(entries, url, linkable=False, max_chars=max_chars)


def doc_uri(doc: SolrDoc) -> str:
    """Return the canonical URL path for a Solr document.

    Prefers view_uri, falls back to id. Strips trailing /index.html
    because Solr document IDs carry it but access.redhat.com 404s on those paths.
    """
    uri = doc.view_uri or doc.id
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
