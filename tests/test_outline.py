"""Tests for okp_mcp.outline: section anchors read from the OKP HTML mirror."""

import httpx
import pytest

from okp_mcp.outline import _html_path
from okp_mcp.outline import NO_OUTLINE
from okp_mcp.outline import OutlineFetcher
from okp_mcp.outline import parse_document
from okp_mcp.outline import parse_outline
from okp_mcp.outline import Section


# Mirrors the shape the OKP appliance serves: a wrapper section around the
# document, chapters nested inside it, and topic sections nested in those.
_GUIDE_HTML = """
<html><body>
<section id="mimir-doc--kafka_configuration_tuning">
  <div class="titlepage"><h1 class="title">Kafka configuration tuning</h1></div>
  <section id="con-config-tuning-intro-str">
    <div class="titlepage"><h1 class="title">Chapter&nbsp;1.&nbsp;Kafka tuning overview</h1></div>
    <p>Admission plugins intercept requests to the control plane API to enforce policy.</p>
    <section id="mapping_properties_and_values">
      <div class="titlepage"><h2 class="title">1.1. Mapping properties and values</h2></div>
    </section>
  </section>
</section>
</body></html>
"""


def test_parse_outline_extracts_anchor_and_title():
    """Each section yields its id paired with the heading it contains."""
    sections = parse_outline(_GUIDE_HTML)
    assert Section("mapping_properties_and_values", "1.1. Mapping properties and values", 2) in sections


def test_parse_outline_drops_document_wrapper():
    """The mimir-doc-- wrapper is the page itself, not a linkable section."""
    anchors = [section.anchor for section in parse_outline(_GUIDE_HTML)]
    assert not any(anchor.startswith("mimir-doc--") for anchor in anchors)


def test_parse_outline_normalizes_entities_and_whitespace():
    """Numbering separators arrive as &nbsp; and must collapse to plain spaces."""
    titles = [section.title for section in parse_outline(_GUIDE_HTML)]
    assert "Chapter 1. Kafka tuning overview" in titles


def test_parse_outline_preserves_document_order():
    """Chapters precede the topics nested inside them."""
    anchors = [section.anchor for section in parse_outline(_GUIDE_HTML)]
    assert anchors.index("con-config-tuning-intro-str") < anchors.index("mapping_properties_and_values")


def test_parse_outline_skips_sections_without_id():
    """A section with no id contributes nothing and does not steal a heading.

    Attributing its heading to the enclosing section would emit a link that
    jumps to the wrong place, which is worse than omitting the entry.
    """
    html = '<section id="outer"><h1 class="title">Outer</h1><section><h2 class="title">Inner</h2></section></section>'
    assert parse_outline(html) == [Section("outer", "Outer")]


def test_parse_outline_ignores_headings_outside_sections():
    """Site chrome headings (nav, footer) sit outside any section."""
    assert parse_outline("<h2>CONTENT</h2><h2>HELP</h2>") == []


def test_parse_outline_keeps_first_heading_per_section():
    """A nested formatting heading must not overwrite the section title."""
    html = '<section id="a"><h1 class="title">Real title</h1><div><h3>Sub label</h3></div></section>'
    assert parse_outline(html) == [Section("a", "Real title")]


def test_parse_outline_empty_document():
    """A page with no sections yields no anchors rather than raising."""
    assert parse_outline("<html><body><p>text</p></body></html>") == []


@pytest.mark.parametrize(
    "doc_id,expected",
    [
        ("/documentation/en-us/guide/index.html", "/documentation/en-us/guide/index.html"),
        ("/documentation/en-us/guide/index", "/documentation/en-us/guide/index/index.html"),
        ("/documentation/en-us/guide/index/", "/documentation/en-us/guide/index/index.html"),
        ("documentation/en-us/guide/index", "/documentation/en-us/guide/index/index.html"),
    ],
    ids=["already-html", "bare-path", "trailing-slash", "missing-leading-slash"],
)
def test_html_path_maps_solr_id_to_mirror_path(doc_id, expected):
    """Solr ids are crawled file paths, so the mapping only fixes the suffix."""
    assert _html_path(doc_id) == expected


def _fetcher(handler) -> OutlineFetcher:
    transport = httpx.MockTransport(handler)
    return OutlineFetcher("http://okp:8080", httpx.AsyncClient(transport=transport))


async def test_fetcher_returns_parsed_sections():
    """A successful mirror fetch is parsed into sections."""
    fetcher = _fetcher(lambda request: httpx.Response(200, text=_GUIDE_HTML))
    outline = await fetcher.get("/documentation/en-us/guide/index.html")
    assert Section("con-config-tuning-intro-str", "Chapter 1. Kafka tuning overview", 1) in outline.sections


async def test_fetcher_requests_the_mirror_path():
    """The request targets the mirror base joined with the document's path."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=_GUIDE_HTML)

    await _fetcher(handler).get("/documentation/en-us/guide/index.html")
    assert seen == ["http://okp:8080/documentation/en-us/guide/index.html"]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404), httpx.Response(500)],
    ids=["not-found", "server-error"],
)
async def test_fetcher_degrades_on_http_error(response):
    """An unavailable mirror yields no anchors instead of failing the tool call."""
    fetcher = _fetcher(lambda request: response)
    assert await fetcher.get("/documentation/en-us/guide/index.html") == NO_OUTLINE


async def test_fetcher_degrades_on_transport_error():
    """A deployment that exposes only Solr must still serve documents."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert await _fetcher(handler).get("/documentation/en-us/guide/index.html") == NO_OUTLINE


async def test_fetcher_disabled_without_base_url():
    """An empty base URL disables the lookup without issuing a request."""
    fetcher = OutlineFetcher("", httpx.AsyncClient())
    assert await fetcher.get("/documentation/en-us/guide/index.html") == NO_OUTLINE


async def test_fetcher_caches_by_doc_id():
    """A repeat lookup is served from cache rather than refetching ~200KB."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=_GUIDE_HTML)

    fetcher = _fetcher(handler)
    first = await fetcher.get("/documentation/en-us/guide/index.html")
    second = await fetcher.get("/documentation/en-us/guide/index.html")
    assert calls == 1
    assert first == second


async def test_fetcher_caches_empty_results():
    """A mirror miss is remembered too, so it costs one request per document."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    fetcher = _fetcher(handler)
    await fetcher.get("/documentation/en-us/guide/index.html")
    await fetcher.get("/documentation/en-us/guide/index.html")
    assert calls == 1


async def test_fetcher_evicts_least_recently_used():
    """The cache stays bounded so a long-lived server cannot pin the corpus."""
    fetcher = _fetcher(lambda request: httpx.Response(200, text=_GUIDE_HTML))
    for index in range(200):
        await fetcher.get(f"/documentation/en-us/guide{index}/index.html")
    assert len(fetcher._cache) == 64


# ---------------------------------------------------------------------------
# DocumentOutline.locate
# ---------------------------------------------------------------------------


def test_locate_places_a_passage_in_its_section():
    """A passage drawn from a section's prose resolves to that section."""
    outline = parse_document(_GUIDE_HTML)
    section = outline.locate("intercept requests to the control plane API to enforce policy")
    assert section is not None
    assert section.anchor == "con-config-tuning-intro-str"


def test_locate_returns_none_for_text_outside_the_body():
    """A ToC fragment exists in Solr's main_content but in no section."""
    outline = parse_document(_GUIDE_HTML)
    assert outline.locate("1. Kafka tuning overview 2. Managed broker configuration 3. Broker tuning") is None


def test_locate_unescapes_entities_before_matching():
    """Solr passages carry literal entities that the mirror text has decoded."""
    html = '<section id="s"><h1 class="title">T</h1><p>run \'/root/x.sh\' now to finish the job</p></section>'
    outline = parse_document(html)
    section = outline.locate("run &#x27;/root/x.sh&#x27; now to finish the job")
    assert section is not None
    assert section.anchor == "s"


def test_locate_probes_past_a_filtered_head():
    """RHV filtering can strip a snippet's opening sentence; later probes recover it."""
    tail = "the supported configuration is documented in the installation guide for this release"
    html = f'<section id="s"><h1 class="title">T</h1><p>Original opening sentence. {tail}</p></section>'
    outline = parse_document(html)
    section = outline.locate(f"A sentence that is not in the page at all. {tail}")
    assert section is not None
    assert section.anchor == "s"


def test_locate_rejects_a_probe_that_is_too_short_to_be_unique():
    """A passage shorter than the minimum probe cannot be placed."""
    assert parse_document(_GUIDE_HTML).locate("Body") is None


def test_locate_on_empty_outline():
    """The shared empty outline places nothing rather than raising."""
    assert NO_OUTLINE.locate("anything at all, some reasonably long text here") is None


def test_parse_document_body_excludes_navigation():
    """Site nav repeats the whole ToC and must not be matchable body text."""
    html = (
        "<nav><a>Chapter 1. Intro</a></nav>"
        '<section id="s"><h1 class="title">Chapter 1. Intro</h1>'
        "<p>Real prose lives here in the body.</p></section>"
    )
    outline = parse_document(html)
    assert "Real prose lives here in the body." in outline.body
    assert outline.body.count("Chapter 1. Intro") == 1
