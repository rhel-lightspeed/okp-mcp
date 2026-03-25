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

## Running with Compose

Start the OKP Solr instance and MCP server together:

```bash
podman login images.paas.redhat.com
podman-compose up -d
```

This pulls the OKP RAG image from `images.paas.redhat.com` (requires authentication) and builds the MCP server container locally.

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

Functional tests run real queries against a live Solr instance and Vertex AI Gemini to verify the MCP server returns accurate RHEL knowledge. They are gated behind the `functional` pytest marker and skipped by default. Scenarios are defined in `tests/functional_cases.py`.

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

Add `-rs` to print why a test was skipped (missing creds, Solr not on `localhost:8983`):

```bash
uv run pytest -m functional -k sap_004 -v -rs
```

Credentials are loaded exclusively from `.env` — bare environment variables are not sufficient. The tests skip gracefully if `.env` is missing, credentials are invalid, or Solr is unavailable.

## License

See [LICENSE](LICENSE) for details.
