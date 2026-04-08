"""SOLR client and query utilities."""

import re

import httpx
from rank_bm25 import BM25Plus  # pyright: ignore[reportMissingImports]

from .config import STOP_WORDS, logger


def _split_quoted_and_plain(text: str) -> list[str]:
    """Split text into an ordered list of tokens: raw words and quoted phrases.

    Quoted phrases are preserved as-is (including the double-quote delimiters).
    Empty quoted phrases ('""') are skipped.
    Unmatched opening quotes cause remaining text to be treated as plain words.
    """
    tokens: list[str] = []
    remainder = text
    while '"' in remainder:
        before, _, rest = remainder.partition('"')
        tokens.extend(before.split())
        if '"' not in rest:
            tokens.extend(rest.split())
            remainder = ""
            break
        phrase, _, remainder = rest.partition('"')
        if phrase:
            tokens.append(f'"{phrase}"')
    tokens.extend(remainder.split())
    return tokens


def _quote_hyphenated_compounds(tokens: list[str]) -> list[str]:
    """Wrap hyphenated compound terms in double quotes for SOLR phrase matching.

    Solr's standard tokenizer splits on hyphens, so ``rpm-ostree`` becomes two
    independent tokens ``rpm`` and ``ostree``.  Quoting forces Solr to match the
    full compound as a phrase, preventing generic ``rpm`` matches from drowning
    out specific ``rpm-ostree`` content.

    Already-quoted tokens and short fragments (≤3 chars) are left untouched.
    """
    return [f'"{t}"' if "-" in t and not t.startswith('"') and len(t) > 3 else t for t in tokens]


_TERM_TRIM_CHARS = "?.,!"


def _normalize_query_token(token: str) -> str:
    """Strip trailing punctuation and lowercase a query token for BM25 matching."""
    return token.lower().rstrip(_TERM_TRIM_CHARS)


def _is_numeric(token: str) -> bool:
    """Return True for numeric version tokens (e.g. '10', '9', '9.4', '4.16')."""
    return bool(re.fullmatch(r"\d+(?:\.\d+)*", _normalize_query_token(token)))


# Patterns for user-environment tokens that add noise to Solr queries.
# IPv4 addresses (with optional CIDR) and common Linux NIC names carry
# configuration-specific details that don't help find relevant docs.
_IP_CIDR_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?$")
_NIC_NAME_RE = re.compile(r"^(?:ens|enp|eth|em)\d", re.IGNORECASE)


def _is_network_noise(token: str) -> bool:
    """Return True for IP addresses, CIDR blocks, and NIC interface names."""
    return bool(_IP_CIDR_RE.match(token) or _NIC_NAME_RE.match(token))


def _clean_query(query: str) -> str:
    """Strip English stopwords and quote hyphenated compounds for SOLR relevance.

    Strips trailing punctuation (including Solr wildcard characters like ``?``),
    preserves quoted phrases intact, and always keeps numeric tokens (e.g. '10',
    '9') since they are critical for version-specific queries in Red Hat content.
    Hyphenated tokens like ``rpm-ostree`` are wrapped in double quotes so SOLR
    matches them as phrases instead of splitting on the hyphen. Falls back to the
    original query if stripping would remove all terms.
    """
    tokens = _split_quoted_and_plain(query)
    parts: list[str] = []
    for t in tokens:
        if t.startswith('"'):
            parts.append(t)
            continue
        # Strip trailing punctuation that doubles as Solr syntax (? is a
        # single-char wildcard, . triggers fuzzy proximity, etc.)
        stripped = t.rstrip(_TERM_TRIM_CHARS)
        if not stripped:
            continue
        # Drop IP addresses, CIDR blocks, and NIC names (e.g. 192.168.1.1/24,
        # ens3) that carry user-specific configuration detail but don't help
        # Solr find relevant documentation.
        if _is_network_noise(stripped):
            continue
        if _is_numeric(stripped) or stripped.lower() not in STOP_WORDS:
            parts.append(stripped)
    # Solr's tokenizer splits hyphens (rpm-ostree -> rpm + ostree), so quote
    # them to force phrase matching. Without this, "rpm" alone floods results
    # with generic RPM package docs, burying actual rpm-ostree content.
    parts = _quote_hyphenated_compounds(parts)
    return " ".join(parts) if parts else query


async def _solr_query(params: dict, client: httpx.AsyncClient | None = None, *, solr_endpoint: str) -> dict:
    """Execute a SOLR query and return the parsed JSON response."""
    close_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
    base_params = {
        # Response format: return results as JSON.
        "wt": "json",
        # Use Extended DisMax, which supports field boosting, phrase boosting, and minimum-match.
        "defType": "edismax",
        # Query fields with boosts: title matches matter most (^5), headings and synopses help,
        # and body content contributes at lower weight.
        "qf": "title^5 main_content heading_h1^3 heading_h2 portal_synopsis allTitle^3 content^2 all_content^1",
        # Phrase boost: reward documents where all query terms appear as an exact phrase.
        "pf": "main_content^5 title^8",
        # Phrase slop for pf: terms may be up to 3 positions apart and still earn the phrase boost.
        "ps": "3",
        # Bigram phrase boost: reward docs where adjacent pairs of query terms appear nearby.
        "pf2": "main_content^3 title^5",
        # Bigram slop: term pairs may be up to 2 positions apart.
        "ps2": "2",
        # Trigram phrase boost: reward docs where consecutive triples of query terms appear nearby.
        "pf3": "main_content^1 title^2",
        # Trigram slop: term triples may be up to 5 positions apart.
        "ps3": "5",
        # --- Highlighting (returns relevant snippets from matched documents) ---
        # Enable highlighting.
        "hl": "on",
        # Generate highlights from the main_content field only.
        "hl.fl": "main_content",
        # Return up to 6 highlighted snippets per document.
        "hl.snippets": "6",
        # Target snippet size in characters (extended to sentence boundaries, see fragsizeIsMinimum).
        "hl.fragsize": "600",
        # Use the Unified Highlighter (fastest, most accurate, supports all options below).
        "hl.method": "unified",
        # Analyze up to 512K characters per document for highlighting (covers long RHEL docs).
        "hl.maxAnalyzedChars": "512000",
        # Break fragments on sentence boundaries rather than mid-sentence.
        "hl.bs.type": "SENTENCE",
        # Use English rules for sentence-boundary detection.
        "hl.bs.language": "en",
        # Treat fragsize as a minimum: snippets grow to the next sentence boundary instead of cutting off.
        "hl.fragsizeIsMinimum": "true",
        # If no query terms match in a document, return a summary from the start of the field.
        "hl.defaultSummary": "true",
        # Use BM25 term weighting to score highlighted passages instead of simple term frequency.
        "hl.weightMatches": "true",
        # Fragment alignment: position each snippet so the match starts roughly 1/3 in from the left,
        # giving context before and after.
        "hl.fragAlignRatio": "0.33",
        # BM25 k1 (term saturation): higher values make repeated terms contribute more to the score.
        "hl.score.k1": "1.0",
        # BM25 b (length normalization): 0.65 moderately penalizes very long documents.
        "hl.score.b": "0.65",
        # BM25 pivot: documents around 200 characters get neutral length treatment;
        # shorter docs score slightly higher, longer ones slightly lower.
        "hl.score.pivot": "200",
        # Minimum match: for 1-2 terms all must match; for 5+ terms at least
        # 75% must match; for 10+ terms relax to 50%.  Long queries (e.g.
        # user-pasted commands with IP addresses and interface names) carry
        # many environment-specific tokens that won't appear in any doc.
        # Without the 10<50% clause, mm=75% on a 15-token query requires 12
        # matches, which no bonding/LACP solution doc can satisfy.
        "mm": "2<-1 5<75% 10<50%",
    }
    base_params.update(params)
    logger.info("SOLR query: q=%r, fq=%r", params.get("q"), params.get("fq"))
    try:
        response = await client.get(solr_endpoint, params=base_params)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        logger.warning("SOLR query timed out after 30s")
        raise
    except httpx.HTTPStatusError as e:
        logger.error("SOLR returned HTTP %d: %s", e.response.status_code, e.response.text[:200])
        raise
    except httpx.RequestError as e:
        logger.error("SOLR connection error: %s", e)
        raise
    except ValueError as e:
        logger.error("SOLR returned non-JSON response: %s", e)
        raise
    finally:
        if close_client:
            await client.aclose()

    _empty_response = {"response": {"numFound": 0, "docs": []}, "highlighting": {}}

    if "error" in data:
        logger.error("SOLR returned error: %s", data["error"])
        return _empty_response
    if "response" not in data or not isinstance(data.get("response", {}).get("docs"), list):
        logger.error("SOLR returned unexpected structure: %s", list(data.keys()))
        return _empty_response

    num_found = data["response"]["numFound"]
    num_docs = len(data["response"]["docs"])
    logger.info("SOLR query matched %d total, returning %d docs", num_found, num_docs)
    return data


_CONTAMINATION_PHRASES = frozenset(
    [
        "fully supported",
        "commonly used",
    ]
)


def _filter_rhv_sentences(text: str, query: str) -> str:
    """Remove sentences that contain both RHV keywords and contamination phrases.

    When a query has no RHV intent, sentences like "SPICE is still fully supported
    in RHV deployments" are misleading for RHEL-focused answers. This filter
    removes such sentences at the sentence level, preserving the rest of the text.
    """
    query_lower = query.lower()
    if any(rhv in query_lower for rhv in _EXTRACTION_DEMOTE_RHV):
        return text

    sentences = re.split(r"(?<=[.!?])\s+|\n", text)
    filtered: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_lower = sentence.lower()
        has_rhv = any(rhv in sentence_lower for rhv in _EXTRACTION_DEMOTE_RHV)
        has_contamination = any(phrase in sentence_lower for phrase in _CONTAMINATION_PHRASES)
        if has_rhv and has_contamination:
            continue
        filtered.append(sentence)
    return " ".join(filtered)


def _get_highlight_snippets(data: dict, *keys: str, query: str = "") -> list[str]:
    """Extract cleaned highlight snippets for a document."""
    hl = data.get("highlighting", {})
    seen: set[str] = set()
    cleaned_snippets: list[str] = []

    for key in keys:
        if key and key in hl:
            snippets = hl[key].get("main_content", [])
            for snippet in snippets:
                clean = re.sub(r"<[^>]+>", "", snippet)
                clean = re.sub(r"\s+", " ", clean).strip()
                clean = _filter_rhv_sentences(clean, query) if query else clean
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                cleaned_snippets.append(clean)

    return cleaned_snippets


def _get_highlights(data: dict, *keys: str, query: str = "") -> str:
    """Extract highlight snippets for a document, trying multiple ID keys."""
    return " ... ".join(_get_highlight_snippets(data, *keys, query=query))


_EXTRACTION_BOOST_KEYWORDS = frozenset(
    [
        "deprecated",
        "removed",
        "no longer",
        "not available",
        "end of life",
        "unsupported",
        "required",
        "must",
        "warning",
        "important",
        "recommended",
        "cockpit",
        "virsh",
        "cockpit-machines",
        "life cycle",
        "full support",
        "maintenance support",
        "extended life",
    ]
)

_EXTRACTION_DEMOTE_RHV = frozenset(
    [
        "red hat virtualization",
        "rhv",
        "rhev",
        "red hat hyperconverged",
    ]
)


def _select_nonoverlapping(
    paragraphs: list[tuple[float, int, str]],
    max_count: int = 3,
    min_gap: int = 500,
) -> list[tuple[int, str]]:
    """Select the top scoring non-overlapping paragraphs.

    Paragraphs must be sorted by score (descending). Returns (position, text)
    pairs sorted by position (ascending) for natural reading order.
    """
    selected: list[tuple[int, str]] = []
    for _score, pos, para in paragraphs:
        if len(selected) >= max_count:
            break
        if all(abs(pos - s) >= min_gap for s, _ in selected):
            selected.append((pos, para))
    selected.sort(key=lambda x: x[0])
    return selected


def _calculate_score_multiplier(para_lower: str, query_lower: str) -> float:
    """Calculate boost/demote multiplier for BM25 scores.

    Returns 2.0x for deprecation keywords, 0.05x for RHV content when query
    lacks RHV intent, 1.0 otherwise.
    """
    multiplier = 1.0
    if any(kw in para_lower for kw in _EXTRACTION_BOOST_KEYWORDS):
        multiplier *= 2.0
    if any(rhv in para_lower for rhv in _EXTRACTION_DEMOTE_RHV) and not any(
        rhv in query_lower for rhv in _EXTRACTION_DEMOTE_RHV
    ):
        multiplier *= 0.05
    return multiplier


def _collect_scored_paragraphs(
    content: str,
    raw_paragraphs: list[str],
    terms: list[str],
    query_lower: str,
    search_start: int,
) -> list[tuple[float, int, str]]:
    """Score paragraphs with BM25 and return those with positive scores.

    Skips paragraphs that start before search_start (e.g. table of contents)
    and filters empty paragraphs before building the BM25 corpus.
    Applies boost/demote multipliers post-scoring: 2x for deprecation keywords,
    0.05x for RHV content when the query has no RHV intent.
    Returns (score, position, text) triples sorted descending by score.
    """
    valid: list[tuple[int, str]] = []
    offset = 0
    for para in raw_paragraphs:
        para_offset = content.find(para, offset)
        offset = para_offset + len(para)
        if para.strip() and para_offset >= search_start:
            valid.append((para_offset, para))

    if not valid:
        return []

    tokenized_corpus = [para.lower().split() for _, para in valid]
    bm25 = BM25Plus(tokenized_corpus)
    scores = bm25.get_scores(terms)

    result: list[tuple[float, int, str]] = []
    for idx, (pos, para) in enumerate(valid):
        base = float(scores[idx])
        if base <= 0:
            continue
        para_lower = para.lower()
        multiplier = _calculate_score_multiplier(para_lower, query_lower)
        result.append((base * multiplier, pos, para))

    result.sort(reverse=True)
    return result


def _format_excerpts(
    selected: list[tuple[int, str]],
    search_start: int,
    per_section: int,
) -> str:
    """Format selected paragraphs into a joined excerpt string.

    Truncates long paragraphs to per_section chars and adds positional markers.
    """
    parts: list[str] = []
    for pos, para in selected:
        excerpt = para[:per_section] + " [...]" if len(para) > per_section else para
        if pos > search_start:
            excerpt = "[...] " + excerpt
        parts.append(excerpt)
    return "\n\n---\n\n".join(parts)


def _extract_relevant_section(content: str, query: str, per_section: int = 1500, max_sections: int = 3) -> str:
    """Extract the most relevant sections using BM25 paragraph scoring.

    Splits content on blank lines (paragraphs), scores each paragraph using
    BM25 (Okapi BM25 via rank-bm25), and returns the top non-overlapping
    paragraphs joined with separator markers. For book-sized documents (>10KB),
    skips the first 5% to avoid matching on the table of contents.

    Paragraphs containing deprecation/critical keywords get a 2x boost, while
    paragraphs about RHV/RHEV are demoted 20x when the query has no RHV intent.
    """
    terms = [
        normalized
        for token in query.split()
        if (normalized := _normalize_query_token(token))
        and (len(normalized) > 3 or token.isupper() or _is_numeric(token))
    ]
    if not terms:
        return content[:per_section]

    search_start = len(content) // 20 if len(content) > 10_000 else 0
    query_lower = query.lower()

    raw_paragraphs = content.split("\n\n")
    if len(raw_paragraphs) < 3:
        raw_paragraphs = content.split("\n")

    scored = _collect_scored_paragraphs(content, raw_paragraphs, terms, query_lower, search_start)
    selected = _select_nonoverlapping(scored, max_count=max_sections)

    if not selected:
        return content[search_start : search_start + per_section]

    return _format_excerpts(selected, search_start, per_section)
