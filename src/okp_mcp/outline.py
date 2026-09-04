"""Section anchors read from the OKP appliance's HTML mirror.

Solr indexes documentation as plain text: ``heading_h1``/``heading_h2`` carry
the section titles but nothing carries the URL fragment each section lives at,
and the fragments cannot be derived from the titles.  Red Hat assigns them in
the AsciiDoc source, so "Kafka tuning overview" is published under
``#con-config-tuning-intro-str``; deriving slugs from heading text was measured
against a live guide and matched 1 heading in 44.

The same appliance that serves Solr also ships the rendered HTML behind its own
httpd, and there each section is a ``<section id="...">`` carrying exactly the
id docs.redhat.com uses as its fragment.  Reading the outline from there keeps
the server offline while still producing links that resolve on the public site:
sampled against the live docs, 45 of a guide's 46 anchors matched byte for byte
(the odd one out is the document wrapper, which is not a section).

Coverage over a 250-document sample of the indexed corpus: every document was
reachable, 236 expose ``<section id>``, and the remaining 14 are single-topic
pages that have no subsections to link to.  Callers fall back to the
title-only outline when this module returns nothing.
"""

import logging
import re

from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser
from typing import NamedTuple

import httpx


logger = logging.getLogger("okp_mcp.outline")

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Collapse every whitespace run to a single space and trim."""
    return _WHITESPACE.sub(" ", text).strip()


# Headings render as <h1 class="title">, <h2 class="title">, ... inside the
# section they name. Anything deeper than h4 is a formatting heading rather
# than a linkable section.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})

# Wrapper element around the whole document; it carries an id but is the page
# itself rather than a section within it, so linking to it is a no-op.
_WRAPPER_ID_PREFIX = "mimir-doc--"

# Script, style, and navigation carry no prose, and the nav in particular
# repeats the whole table of contents -- exactly the text a passage must not
# be matched against.
_NON_PROSE_TAGS = frozenset({"script", "style", "nav"})

# Probe window used to find a passage in the body. Long enough to be unique in
# a 150KB guide, short enough to survive a snippet boundary.
_PROBE_CHARS = 60
_MIN_PROBE_CHARS = 25

# Each entry holds the page's body text (~150KB for the largest guides) and
# costs a ~200KB fetch to build, so the cache trades memory for both. Sized to
# keep a working set of guides resident while bounding the process at a few
# tens of MB.
_CACHE_SIZE = 64


class Section(NamedTuple):
    """A linkable section: the URL fragment and the heading it belongs to.

    ``level`` is the nesting depth (1 for a chapter, 2 for a section within
    it, ...), which lets callers shed the deepest levels first when an
    outline is too large to render whole.
    """

    anchor: str
    title: str
    level: int = 1


class DocumentOutline(NamedTuple):
    """A page's sections plus the body text needed to place a passage in one."""

    sections: tuple[Section, ...] = ()
    # Whitespace-collapsed text of every section, in document order. Solr's
    # main_content cannot stand in for it: that field leads with the page's
    # table of contents, which repeats most headings, so offsets computed
    # against it attribute passages to whichever heading the ToC listed.
    body: str = ""
    # (offset into body, anchor) at each section start, ascending.
    starts: tuple[tuple[int, str], ...] = ()

    def locate(self, passage: str) -> Section | None:
        """Return the section a passage came from, or None if it has no home.

        Solr highlights ``main_content``, which includes the table of contents,
        so a passage can legitimately be a run of headings that exists nowhere
        in the body. Measured over 146 highlight passages from 33 guides, every
        one of the 104 drawn from real prose was placed correctly and all 42
        misses were ToC fragments -- so None means "not body text", not
        "lookup failed".
        """
        offset = self._find(passage)
        if offset < 0:
            return None

        found = None
        for start, anchor in self.starts:
            if start > offset:
                break
            found = anchor
        if found is None:
            return None
        return next((s for s in self.sections if s.anchor == found), None)

    def _find(self, passage: str) -> int:
        """Locate a passage in the body, probing a few points along it.

        A single leading probe is not enough: highlight snippets have had RHV
        sentences filtered out of them, so the head of a snippet is not always
        contiguous in the source.  Probing further in recovers those.
        """
        text = _normalise(unescape(passage))
        for fraction in (0.0, 0.35, 0.6, 0.8):
            probe = text[int(len(text) * fraction) :][:_PROBE_CHARS]
            if len(probe) < _MIN_PROBE_CHARS:
                continue
            offset = self.body.find(probe)
            if offset >= 0:
                return offset
        return -1


# Shared empty outline for every "no anchors available" path: an unconfigured
# or unreachable mirror, a non-documentation document, a page with no sections.
NO_OUTLINE = DocumentOutline()


class _OutlineParser(HTMLParser):
    """Collect sections, their titles, and their body text from a guide.

    Sections nest, so the parser keeps a stack and attributes a heading to the
    innermost section open when it starts.  Sections without an ``id`` still
    push onto the stack -- dropping them would misattribute their headings to
    the enclosing section, which would produce a link to the wrong place.

    Body text is accumulated whitespace-collapsed as it arrives so that section
    offsets are recorded against the final string, rather than normalising
    afterwards and having to re-derive every offset.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._open_sections: list[str | None] = []
        self._collecting: str | None = None
        self._level = 1
        self._buffer: list[str] = []
        self._skip = 0
        self._words: list[str] = []
        self._length = 0
        self.sections: list[Section] = []
        self.starts: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _NON_PROSE_TAGS:
            self._skip += 1
            return
        if tag == "section":
            anchor = dict(attrs).get("id")
            self._open_sections.append(anchor)
            if anchor:
                self.starts.append((self._length, anchor))
            return
        if tag in _HEADING_TAGS and self._open_sections:
            anchor = self._open_sections[-1]
            # One heading per section: a nested formatting heading inside an
            # already-titled section must not overwrite the section's title.
            if anchor and not any(existing.anchor == anchor for existing in self.sections):
                self._collecting = anchor
                self._level = len(self._open_sections)
                self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _NON_PROSE_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "section":
            if self._open_sections:
                self._open_sections.pop()
            return
        if tag in _HEADING_TAGS and self._collecting:
            title = _normalise("".join(self._buffer))
            if title:
                self.sections.append(Section(self._collecting, title, self._level))
            self._collecting = None

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._buffer.append(data)
        if self._skip or not self._open_sections:
            return
        for word in data.split():
            if self._words:
                self._words.append(" ")
                self._length += 1
            self._words.append(word)
            self._length += len(word)

    def body(self) -> str:
        return "".join(self._words)


def parse_document(html: str) -> DocumentOutline:
    """Parse a rendered documentation page into sections and locatable body text.

    Levels are normalised so the shallowest section returned is level 1,
    because the dropped document wrapper otherwise pushes every real chapter
    down a level on the pages that have one.
    """
    parser = _OutlineParser()
    parser.feed(html)

    sections = [section for section in parser.sections if not section.anchor.startswith(_WRAPPER_ID_PREFIX)]
    if not sections:
        return NO_OUTLINE

    offset = min(section.level for section in sections) - 1
    kept = {section.anchor for section in sections}
    return DocumentOutline(
        sections=tuple(section._replace(level=section.level - offset) for section in sections),
        body=parser.body(),
        starts=tuple((start, anchor) for start, anchor in parser.starts if anchor in kept),
    )


def parse_outline(html: str) -> list[Section]:
    """Extract just the linkable sections from a rendered documentation page."""
    return list(parse_document(html).sections)


def _html_path(doc_id: str) -> str:
    """Map a Solr document id onto its path in the HTML mirror.

    Solr ids are the crawled file paths, so the mapping is the identity apart
    from the ``/index.html`` suffix that ``doc_uri`` strips for display.
    """
    path = doc_id if doc_id.startswith("/") else f"/{doc_id}"
    return path if path.endswith(".html") else f"{path.removesuffix('/')}/index.html"


class OutlineFetcher:
    """Fetches and caches section outlines from the HTML mirror.

    Every failure mode -- no mirror configured, mirror unreachable, document
    absent, page carrying no sections -- resolves to an empty outline.  The
    outline is a navigational extra, so it must never turn a working
    ``get_document`` call into an error.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._cache: OrderedDict[str, DocumentOutline] = OrderedDict()

    async def get(self, doc_id: str) -> DocumentOutline:
        """Return a document's outline, or an empty one if unavailable."""
        if not self._base_url:
            return NO_OUTLINE

        if doc_id in self._cache:
            self._cache.move_to_end(doc_id)
            return self._cache[doc_id]

        outline = await self._fetch(doc_id)

        self._cache[doc_id] = outline
        self._cache.move_to_end(doc_id)
        if len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        return outline

    async def _fetch(self, doc_id: str) -> DocumentOutline:
        url = f"{self._base_url}{_html_path(doc_id)}"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Debug, not warning: a deployment that exposes only Solr hits this
            # on every documentation fetch and the fallback is well defined.
            logger.debug("outline fetch failed for %s: %s", url, exc)
            return NO_OUTLINE

        try:
            return parse_document(response.text)
        except (ValueError, AssertionError) as exc:
            logger.debug("outline parse failed for %s: %s", url, exc)
            return NO_OUTLINE
