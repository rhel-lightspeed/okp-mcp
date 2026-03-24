# okp-mcp

MCP server for the Red Hat Offline Knowledge Portal (OKP). Bridges LLM tool calls to the OKP Solr index so agents can search RHEL documentation, CVEs, errata, solutions, and articles.

## Quickstart

Install dependencies:

```
uv sync
```

Run locally (stdio transport, default):

```
uv run okp-mcp
```

Run with HTTP transport:

```
uv run okp-mcp --transport streamable-http --port 8000
```

## Configuration

Settings come from CLI arguments and `MCP_*` environment variables. CLI args take precedence.

| Setting | Env var | Default | Description |
|---------|---------|---------|-------------|
| `--transport` | `MCP_TRANSPORT` | `streamable-http` | `stdio`, `sse`, or `streamable-http` |
| `--host` | `MCP_HOST` | `0.0.0.0` | Bind address for HTTP transports |
| `--port` | `MCP_PORT` | `8000` | Bind port for HTTP transports |
| `--log-level` | `MCP_LOG_LEVEL` | `INFO` | Python log level |
| `--solr-url` | `MCP_SOLR_URL` | `http://localhost:8983` | Base URL of the Solr instance |

Run `okp-mcp --help` for the full list.

### Pinning documents in `search_documentation` (debug)

To check whether **forcing a Solr document into the first search results** fixes the LLM answer (before changing ranking), set:

```bash
export MCP_PIN_SEARCH_DOCS='/documentation/en-us/red_hat_enterprise_linux_for_sap_solutions/9/html-single/red_hat_enterprise_linux_system_roles_for_sap/index/index.html'
```

Use comma-separated Solr `id` values (same as `get_document` / `view_uri` paths). Each pinned doc is fetched with the **same query** as the user search so Solr highlights / BM25 excerpts align with that query. A banner is prepended to the tool output. **Unset in production.**

If the pinned excerpt is still boilerplate (e.g. legal notice), append extra terms **only for the pinned fetch** (not the main search):

```bash
export MCP_PIN_SEARCH_QUERY_SUFFIX='sap_general_preconfigure sap_hana_preconfigure sap_netweaver_preconfigure'
```

`get_document` / pinned fetch responses now request Solr field `id` so highlight maps match the document key; if `view_uri` is empty in the index, the pinned path is used for the portal URL.

## Running with Compose

Start the OKP Solr instance and MCP server together:

```
podman-compose up -d
```

This pulls the official OKP image from `registry.redhat.io` (requires `podman login registry.redhat.io` first) and builds the MCP server container locally.

Build the MCP server image:

```
podman build -t okp-mcp -f Containerfile .
```

## Development

Install dev dependencies:

```
uv sync --group dev
```

Run the full CI suite locally:

```
make ci
```

Individual targets:

```
make lint        # ruff check
make format      # ruff format
make typecheck   # ty check
make radon       # cyclomatic complexity gate (A/B only)
make test        # pytest with coverage
```

## Functional Tests

Functional tests run real queries against a live Solr instance and Vertex AI Gemini to verify the MCP server returns accurate RHEL knowledge. They are gated behind the `functional` pytest marker and skipped by default. Scenarios are defined in `tests/functional_cases.py` (e.g. RSPEED CLA tickets and eval ids such as `sap_004`).

Prerequisites:

- OKP Solr container running on `localhost:8983`
- Google Cloud service account JSON with Vertex AI access
- GCP project ID
- (Optional) `OKP_FUNCTIONAL_MODEL` in `.env` to override the Gemini model (default: `gemini-2.5-flash`)

Set up credentials:

```bash
cp .env.example .env
# Edit .env with your real values
```

Run them:

```bash
uv run pytest -m functional -v
```

Add `-rs` to print **why** a test was skipped (missing creds, `GOOGLE_CLOUD_PROJECT`, or Solr not on `localhost:8983`):

```bash
uv run pytest -m functional -k sap_004 -v -rs
```

Credentials are loaded exclusively from `.env` — bare environment variables are not sufficient. The tests skip gracefully if `.env` is missing, credentials are invalid, or Solr is unavailable.

**Org policy blocks the default Gemini model:** Some GCP organizations restrict Vertex models via `constraints/vertexai.allowedModels`. If the run fails with `FAILED_PRECONDITION` / `disallowed Gen AI model gemini-2.5-flash`, either ask your admin to allow `publishers/google/models/gemini-2.5-flash:predict`, or set **`OKP_FUNCTIONAL_MODEL`** in `.env` to a model your policy already allows (see `.env.example` for examples).

## License

See [LICENSE](LICENSE) for details.
