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

## License

See [LICENSE](LICENSE) for details.
