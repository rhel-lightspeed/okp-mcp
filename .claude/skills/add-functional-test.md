# Add Functional Test Case for Incorrect Answers

Workflow for converting a CLA incorrect-answer ticket into a functional test case in `tests/functional_cases.py`, with search quality fixes when needed.

## Prerequisites

- Solr container running locally (`podman-compose up -d`): `http://localhost:8983/solr/portal/select`
- Familiarity with Solr exploration: see `docs/SOLR_EXPLORATION.md`

## Step 1: Identify the ticket

Find the RSPEED ticket with the `cla-incorrect-answer` label. Extract:

- The **question** the user asked
- The **correct answer** (from the ticket description or comments)
- The **source documents** that contain the correct answer (article IDs, solution IDs, doc page names)

Cross-check against existing entries in `tests/functional_cases.py` to avoid duplicates. Existing tests use `id="RSPEED_XXXX"` so grep for the ticket number. Also check for open PRs that may already cover this ticket:

```bash
gh pr list --search "RSPEED-XXXX" --state open
```

## Step 2: Find the right documents in Solr

Use `curl` against the local Solr instance to find documents that contain the correct answer. Useful query patterns:

```bash
# Search by document slug (article/solution number)
curl -s 'http://localhost:8983/solr/portal/select' \
  -G --data-urlencode 'q=url_slug:7092011' \
  --data-urlencode 'fl=id,url_slug,title,documentKind,main_content' \
  --data-urlencode 'rows=1' --data-urlencode 'wt=json'

# Full-text search scoped to docs/articles/solutions
curl -s 'http://localhost:8983/solr/portal/select' \
  -G --data-urlencode 'q=GFS2 removed RHEL 10' \
  --data-urlencode 'fq=documentKind:(documentation OR article OR solution)' \
  --data-urlencode 'fl=id,url_slug,title,documentKind,score' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'

# Phrase search for exact matches
curl -s 'http://localhost:8983/solr/portal/select' \
  -G --data-urlencode 'q="GFS2" "RHEL 10"' \
  --data-urlencode 'fl=id,url_slug,title,documentKind,score' \
  --data-urlencode 'rows=10' --data-urlencode 'wt=json'
```

Pipe results through `python3 -m json.tool` or use a Python one-liner to extract specific fields. When examining `main_content`, search for relevant keywords using Python string slicing around `str.find()` hits rather than dumping the entire field.

Record the document IDs (url_slug like `7092011`, or path-based identifiers like `considerations_in_adopting_rhel_10`) and note which content fragments contain the answer.

## Step 3: Test what `_run_portal_search()` actually returns

Before writing the test case, check what the portal search pipeline returns for the ticket's question:

```python
uv run python -c "
import asyncio, httpx
from okp_mcp.config import ServerConfig
from okp_mcp.portal import _run_portal_search

async def main():
    config = ServerConfig()
    async with httpx.AsyncClient(timeout=30.0) as client:
        chunks, overflow = await _run_portal_search(
            'Is GFS2 available in RHEL 10?',
            client=client,
            solr_endpoint=config.solr_endpoint,
        )
        for i, c in enumerate(chunks):
            print(f'{i+1}. parent_id={c.parent_id}')
            print(f'   title={c.title}')
            print(f'   chunk (first 200): {c.chunk[:200]}')
            print()

asyncio.run(main())
"
```

This reveals whether the right documents surface or if search quality tuning is needed.

## Step 4: Create the FunctionalCase entry

Add a new `pytest.param` entry at the end of the `FUNCTIONAL_TEST_CASES` list in `tests/functional_cases.py`:

```python
# Verified against live Solr YYYY-MM-DD: PASS
pytest.param(
    FunctionalCase(
        question="Is GFS2 available in RHEL 10?",
        expected_docs=[
            "7092011",           # url_slug or path substring
            "3290201",
            "considerations_in_adopting_rhel_10",  # path substring
        ],
        expected_content=[
            ("removed", "discontinued"),   # tuple = any alternative matches
            "resilient storage",           # plain string = exact substring
            "gfs2",
        ],
    ),
    id="RSPEED_2794",
),
```

**Field rules:**

- `expected_docs`: substrings matched case-insensitively against `parent_id`, `doc_id`, `title`, and `online_source_url` of every returned chunk. At least one entry must match at least one result.
- `expected_content`: substrings checked against the combined chunk text (case-insensitive). Plain strings must match exactly. Tuples mean any one alternative must match. Use tuples when the same concept appears in different phrasings across documents.
- `max_position`: optional, only if a specific doc MUST appear in the top N.
- `max_result_count`: optional, only if too many results indicate a problem.

## Step 5: Run the functional test

```bash
# Run just the new test
uv run pytest -m functional -v -k "RSPEED_2794" tests/test_functional.py

# Run all functional tests to check for regressions
uv run pytest -m functional -v tests/test_functional.py
```

If the test passes, update the comment to `# Verified against live Solr YYYY-MM-DD: PASS` and you're done (skip to Step 7).

If it fails, proceed to Step 6.

## Step 6: Fix search quality with an IntentRule

When `_run_portal_search()` returns the wrong documents, the fix is usually adding an `IntentRule` in `src/okp_mcp/intent.py`.

### Diagnose the problem

Common failure patterns:

- **Generic term drowning**: high-frequency words in the query (like "available", "supported", "how to") match thousands of unrelated docs, pushing specific content out of the top 10
- **No intent match**: the query's key technical term has no IntentRule, so only base Solr field weights apply
- **Wrong intent match**: a more generic intent fires before the correct specific one (first-match-wins)

### Add an IntentRule

```python
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
```

**IntentRule fields:**

| Field | Purpose | When to use |
|---|---|---|
| `name` | Logging identifier | Always |
| `pattern` | Regex matched against lowercased query | Always |
| `bq` | Solr boost query injected into main search | Always (this is the primary lever) |
| `highlight_terms` | Extra terms appended to `hl.q` for snippet selection | When Solr picks the wrong passages |
| `dep_title_terms` | OR-joined terms for deprecation query allTitle boost | When query involves deprecated/removed features |
| `dep_content_terms` | OR-joined terms for deprecation query main_content boost | Same as above (must set both or neither) |

**Placement rules:**

- Insert at the correct priority position in `INTENT_RULES` (most specific first, most generic last)
- First match wins: if a query could match multiple intents, the more specific one must come first
- Update the priority rationale comment block at the top of the list
- The `vm` intent should always be last (broadest catch-all)

**Boost weight guidelines:**

- `allTitle` boosts: `^30` to `^100` for strong title-based re-ranking
- `main_content` boosts: `^10` to `^15` for content signal
- `dep_title_terms` / `dep_content_terms`: always use low weights (`^5` / `^3`) to avoid inflating deprecation scores above main query scores

### Iterate

After adding the IntentRule, re-run the functional test. If it still fails, adjust boost weights or terms. Run all functional tests after each change to catch regressions.

## Step 7: Verify everything

```bash
# All functional tests pass
uv run pytest -m functional -v tests/test_functional.py

# Unit tests pass
uv run pytest tests/ --ignore=tests/test_functional.py

# Lint and typecheck
make lint
make typecheck
```

### Optional: check okp-mcp container logs

If the okp-mcp container is running, check the logs for correct intent detection and query behavior:

```bash
podman logs --since 2m okp-mcp
```

Look for:
- `Intent boost: applied 'gfs2' to main query` confirming the intent fired
- `search_portal: query=...` showing what the LLM sent
- `Portal search: ... returned=N` showing how many chunks came back
- `Score filter: dropped N/M chunks` showing noise removal
