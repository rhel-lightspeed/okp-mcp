# Incorrect Answer Loop

Repeatable process for turning RSPEED "incorrect answer" tickets into functional tests and fixing the MCP server until all tests pass.

## Prerequisites

- OKP Solr container running on `localhost:8983` (`podman-compose up -d`)
- Vertex AI credentials configured in `.env` (see README)
- `uv sync` completed

## Step 1: Find the Next Ticket

Query Jira for the most recently created RSPEED ticket with the `cla-incorrect-answer` label:

```jql
project = RSPEED AND labels = "cla-incorrect-answer" AND status != Closed ORDER BY created DESC
```

Fields needed: `summary,labels,status,description`

Skip tickets that aren't about factual knowledge errors:
- Jailbreak/prompt-injection issues
- Model guardrail/scope issues (e.g., whether CLA should refuse to answer a question entirely)
- Problems that require changes outside this repo

Cross-reference against existing test case IDs in `tests/functional_cases.py`:

```bash
grep 'id="RSPEED_' tests/functional_cases.py
```

Also check unmerged PRs for test cases in flight:

```bash
gh pr list --state open --json number,title,headRefName
```

Skip any ticket that already has a test case or an open PR. Take the most recently created ticket that doesn't.

Read the ticket description to extract:
- The question CLA answered incorrectly
- What the correct answer should be
- Any specific documents or facts mentioned

## Step 1b: Create a Working Branch

Create a branch named after the Jira ticket (lowercase):

```bash
git checkout -b rspeed-<ticket_number>
```

All changes for this ticket (test case, code fixes) should be committed on this branch.

### Batching multiple tickets

When processing several tickets at once, use a combined branch name (e.g., `rspeed-2200-2136`). This works well when most or all tests pass immediately without code fixes, since the test-only changes can ship in a single PR. If a ticket needs a code fix, split it to its own branch/PR to keep the fix isolated and reviewable.

## Step 2: Build the Test Case

### 2a. Find the Best Solr Documents

Query the local Solr instance directly to find documents that correctly answer the question. Use curl against the `/select` endpoint:

```bash
curl -s "http://localhost:8983/solr/portal/select" \
  --data-urlencode "q=<search terms from the question>" \
  --data-urlencode "fq=documentKind:(documentation OR solution OR article OR access-drupal10-node-type-page)" \
  --data-urlencode 'fq=-product:"Red Hat Virtualization"' \
  --data-urlencode "fl=id,allTitle,view_uri,documentKind,product,documentation_version,score" \
  --data-urlencode "rows=10" \
  --data-urlencode "hl=on" \
  --data-urlencode "hl.fl=main_content" \
  --data-urlencode "hl.snippets=3" \
  --data-urlencode "hl.fragsize=200" \
  --data-urlencode "wt=json" \
  | python3 -m json.tool
```

Tips for finding the right documents:
- Try multiple query variations (keywords, phrases, specific terms from the correct answer)
- Look for documents with high relevance scores
- Prefer documents that explicitly state the correct answer
- Check the `highlighting` section in search results. It often contains enough context (key sentences, data points) to confirm a document has the right content without a separate full-content fetch.
- Check `view_uri` and `id` values, these become `expected_doc_refs`
- If highlights aren't conclusive, retrieve full content: `curl -s "http://localhost:8983/solr/portal/select" --data-urlencode 'q=*:*' --data-urlencode 'fq=id:"<doc_id>"' --data-urlencode 'fl=allTitle,main_content,view_uri' --data-urlencode 'wt=json' | python3 -m json.tool` (note: not all fields are populated for every document type, so some may return empty)

Select up to 3 documents maximum for `expected_doc_refs`. Use document IDs, URL slugs, or distinctive substrings from the `view_uri` or `id` fields.

### 2b. Add to functional_cases.py

Add a new `pytest.param` entry to `FUNCTIONAL_TEST_CASES` in `tests/functional_cases.py`:

```python
pytest.param(
    FunctionalCase(
        question="<exact question from the ticket>",
        expected_doc_refs=[
            "<doc_id_or_uri_substring_1>",
            "<doc_id_or_uri_substring_2>",
            "<descriptive_keyword>",
        ],
        required_facts=["<fact 1>", ("alt_a", "alt_b"), "<fact 3>"],
        forbidden_claims=["<the incorrect claim CLA made>"],
    ),
    id="RSPEED_<ticket_number>",
),
```

Field reference:
- `question`: The exact question CLA got wrong. Copy from the ticket.
- `expected_doc_refs`: Up to 3 substrings that should appear in Solr results or the final response. Can be document IDs (e.g., `"6955095"`), URI fragments (e.g., `"rhel-container-compatibility"`), or descriptive terms (e.g., `"compatibility matrix"`). At least one must match.
- `required_facts`: Facts that MUST appear in the LLM response. Plain strings require exact substring match (case-insensitive). Tuples mean any one alternative must match (OR logic).
- `forbidden_claims`: Phrases from the incorrect answer that must NOT appear in the response. These catch regressions to the wrong answer.

#### Writing good `required_facts`

Require **data points**, not **phrasing**. The LLM may state the correct answer without using the exact terminology you expect. For example:
- Good: `"9.0"`, `"9.2"`, `"24 months"` (concrete data the response must contain)
- Bad: `"even-numbered"` (the LLM might correctly list 9.0, 9.2, 9.4, 9.6 without ever saying "even-numbered")

Use tuples for facts that can be stated multiple ways: `("deprecated", "removed")`, `("48 months", "4 years")`.

#### Writing good `forbidden_claims`

Forbidden claims must be phrases that are **wrong regardless of context**. Avoid phrases that could appear in a correct response with different surrounding words:
- Good: `"9.0 did not have EUS"` (definitively wrong, 9.0 did have EUS)
- Good: `"viable strategy"`, `"fully supported and commonly used"` (distinctive wrong-answer phrasing)
- Risky: `"9.0 does not have EUS"` (a correct response might say "9.0 EUS does not have remaining coverage" or "9.0 EUS is no longer active")

When in doubt, rely on `required_facts` to verify correctness rather than `forbidden_claims` to catch incorrectness. A correct answer naturally excludes the wrong one.

## Step 3: Run the Functional Tests

Run the single new test case first:

```bash
uv run pytest -m functional -k "RSPEED_<number>" -v
```

Two outcomes:

1. **Test passes**: The MCP server already handles this question correctly. No code fix is needed. The test case locks in this correct behavior to prevent future regressions. Skip Step 4 and proceed to Step 5.
2. **Test fails**: Expected. Proceed to Step 4.

## Step 4: Fix Until the New Test Passes

This is an iterative loop: diagnose, fix, rerun, repeat.

**Before debugging the MCP server**, make sure the test case itself isn't over-constrained. If the LLM response is factually correct but fails a `required_facts` check, relax the test case first (see "Writing good required_facts" above).

### What to examine

The MCP server code that affects search results and LLM behavior:

| File | What it controls |
|------|-----------------|
| `src/okp_mcp/tools.py` | Search queries, Solr parameters, boost queries, filters, result assembly |
| `src/okp_mcp/solr.py` | Query cleaning, highlighting, section extraction |
| `src/okp_mcp/content.py` | Boilerplate stripping from document content |
| `src/okp_mcp/formatting.py` | Result formatting, sort keys, deprecation detection |
| `tests/fixtures/functional_system_prompt.txt` | LLM system prompt (instructions that shape how the model interprets results) |

### Common failure patterns and fixes

| Symptom | Likely cause | Fix area |
|---------|-------------|----------|
| Wrong documents returned | Boost queries (`bq`) not prioritizing correct docs | `_build_search_queries()` in `tools.py` |
| Right docs returned but LLM ignores them | System prompt doesn't instruct the model to handle this case | `functional_system_prompt.txt` |
| Key terms getting stripped from query | Query cleaning too aggressive | `_clean_query()` in `solr.py` |
| Relevant content truncated | Section extraction or formatting cutting off key info | `solr.py` or `formatting.py` |
| Deprecated feature recommended as available | Deprecation boost/detection insufficient | `bq` params or `_detect_*` functions in `tools.py` |

### Iteration loop

1. Run the failing test: `uv run pytest -m functional -k "RSPEED_<number>" -v`
2. Read the failure output. Note which assertion failed (`required_facts`, `forbidden_claims`, `expected_doc_refs`).
3. If `expected_doc_refs` failed: the Solr query isn't returning the right documents. Adjust `_build_search_queries()` parameters.
4. If `required_facts` failed: the LLM response is missing key information. Check if the documents contain the info (Solr curl), then check if formatting/highlighting is surfacing it.
5. If `forbidden_claims` failed: the LLM is still giving the wrong answer. Strengthen the system prompt guidance or improve deprecation/correction signals in the search results.
6. Make the fix, rerun the single test. Repeat until it passes.

## Step 5: Verify All Tests Pass

Once the new test passes, run ALL functional tests:

```bash
uv run pytest -m functional -v
```

If any previously passing test now fails, it must be fixed before proceeding. This is non-negotiable.

Common causes of regressions:
- System prompt changes that are too specific (fix one case, break another)
- Boost query changes that demote previously correct results
- Query cleaning changes with unintended side effects

If a fix helps one test but hurts another, find a more targeted approach. Broad changes to boost weights or system prompt instructions are the most common source of regressions.

Keep iterating until every functional test passes.

## Step 6: Run Full CI and Commit

Run the full CI suite to catch lint, type, and complexity issues:

```bash
make ci
```

Fix any failures. Then stage only the files you changed and commit:

```bash
git add tests/functional_cases.py src/
git commit -s -S -m "fix: handle incorrect CLA answer for RSPEED-<number>

Add functional test case for '<brief description of the question>'.
<Brief description of the code change that fixed it, if any.>"
```

If no code fix was needed (test passed immediately in Step 3), adjust the commit message accordingly:

```bash
git add tests/functional_cases.py
git commit -s -S -m "test: add functional test for RSPEED-<number>

Lock in correct CLA behavior for '<brief description of the question>'.
MCP server already returns the right answer; no code change needed."
```

Ask the user to review the changes before pushing or creating a pull request.
