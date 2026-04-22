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

## Running Locally

Run the OKP Solr index and MCP server together using a podman pod.

### Prerequisites

- [Podman](https://podman.io/) installed
- Authenticated to `registry.redhat.io` (`podman login registry.redhat.io`)
- An OKP access key from <https://access.redhat.com/offline/access>

### 1. Create a pod

The pod groups both containers into a shared network namespace so they communicate via `localhost`. Ports are exposed at the pod level.

```bash
podman pod create --name okp -p 8983:8983 -p 8000:8000
```

### 2. Start the OKP Solr index

```bash
podman run -d --pod okp --name redhat-okp \
  -e ACCESS_KEY=<your-access-key> \
  -e SOLR_JETTY_HOST=0.0.0.0 \
  registry.redhat.io/offline-knowledge-portal/rhokp-rhel9:latest
```

The first start downloads and indexes content (~10 GB image, may take several minutes). Watch progress with:

```bash
podman logs -f redhat-okp
```

Wait until you see `Started Solr server on port 8983`. Subsequent starts of the same container (`podman pod stop okp` / `podman pod start okp`) are faster because the index is cached. Removing the pod (`podman pod rm -f okp`) deletes the container and its index — the next `podman run` will re-download. To persist the index across recreations, add a named volume: `-v okp-solr-data:/opt/solr/server/solr/portal/data`.

### 3. Start the MCP server

```bash
podman run -d --pod okp --name okp-mcp \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_SOLR_URL=http://localhost:8983 \
  quay.io/redhat-user-workloads/rhel-lightspeed-tenant/okp-mcp
```

### 4. Verify

Confirm Solr has data:

```bash
curl -s "http://localhost:8983/solr/portal/select?q=*:*&rows=0" | python3 -m json.tool
```

You should see `numFound` with a large number of documents (600k+).

Confirm the MCP server responds:

```bash
curl -s -N -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}, "id": 1}'
```

You should see a response with `serverInfo.name: "RHEL OKP Knowledge Base"`.

### Cleanup

```bash
podman pod rm -f okp
```

### Alternative: podman-compose

A `podman-compose.yml` is included for development use. It builds from source and is useful for local iteration, but note that `podman-compose` is not supported on RHEL.

```bash
OKP_ACCESS_KEY=<your-access-key> podman-compose up -d
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

Functional test scenarios are defined in `tests/functional_cases.py`. They are gated behind the `functional` pytest marker and deselected by default. Run them with `uv run pytest -m functional -v` (requires a running Solr instance).

## License

See [LICENSE](LICENSE) for details.
