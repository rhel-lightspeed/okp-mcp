# Running OKP with systemd quadlets

Podman quadlet files that run the OKP Solr index and MCP server as rootless systemd services. This gives you automatic restart on failure, journald logging, and start-at-login with no manual `podman run` commands.

## Prerequisites

- Podman 4.4+ (quadlet support)
- Authenticated to `registry.redhat.io` (`podman login registry.redhat.io`)
- An OKP access key from <https://access.redhat.com/offline/access>

## Setup

### 1. Create the environment file

The Solr container reads its access key from a file rather than embedding it in the quadlet:

```bash
mkdir -p ~/.config/okp-mcp
cat > ~/.config/okp-mcp/solr.env << 'EOF'
ACCESS_KEY=<your-access-key>
EOF
chmod 600 ~/.config/okp-mcp/solr.env
```

### 2. Install the quadlet files

Copy all quadlet files into a subdirectory under the rootless systemd path:

```bash
mkdir -p ~/.config/containers/systemd/okp-mcp
cp quadlet/*.{container,network,volume} ~/.config/containers/systemd/okp-mcp/
```

### 3. Reload and start

Tell systemd to pick up the new quadlet files, then start the MCP server (which pulls in Solr automatically via its dependency):

```bash
systemctl --user daemon-reload
systemctl --user start okp-mcp
```

The first start pulls the Solr image (~10 GB) and indexes content. This takes several minutes. Watch progress with:

```bash
journalctl --user -xeu okp-solr -f
```

Wait until you see `Started Solr server on port 8983`.

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

## Management

Start/stop both containers (dependency ordering is automatic):

```bash
systemctl --user start okp-mcp
systemctl --user stop okp-mcp
```

The quadlet files include `[Install] WantedBy=default.target`, so both services start automatically when you log in. No `systemctl enable` is needed for quadlet-generated units.

To keep services running after logout (and start them at boot without logging in), enable lingering:

```bash
loginctl enable-linger "$USER"
```

View logs:

```bash
journalctl --user -xeu okp-solr
journalctl --user -xeu okp-mcp
```

Check status:

```bash
systemctl --user status okp-solr okp-mcp
```

## Automatic image updates

The MCP server container is configured with `AutoUpdate=registry`. To enable periodic checks for new images, start the podman auto-update timer:

```bash
systemctl --user enable --now podman-auto-update.timer
```

Without this timer, the auto-update label is present but nothing triggers it.

## Troubleshooting

### Dry-run the quadlet generator

If the services don't appear after `daemon-reload`, verify the generator can parse the quadlet files:

```bash
/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun
```

This prints the generated unit files or errors explaining what went wrong.

### Service won't start

Check the full journal output for the failing service:

```bash
journalctl --user -xeu okp-solr --no-pager
```

Common issues:
- Missing `~/.config/okp-mcp/solr.env` (create it per step 1)
- Not logged into `registry.redhat.io` (`podman login registry.redhat.io`)
- Port conflict on 8983 or 8000 (stop any existing Solr or MCP containers)

## Cleanup

Stop the services:

```bash
systemctl --user stop okp-solr okp-mcp
```

Remove the quadlet files and reload:

```bash
rm -r ~/.config/containers/systemd/okp-mcp
systemctl --user daemon-reload
```

Optionally remove the Solr data volume and environment file:

```bash
podman volume rm okp-solr-data
rm -r ~/.config/okp-mcp
```

## Files

| File | Purpose |
|------|---------|
| `okp.network` | Shared podman network for container DNS resolution |
| `okp-solr-data.volume` | Persistent storage for the Solr search index |
| `okp-solr.container` | OKP Solr search engine |
| `okp-mcp.container` | OKP MCP server (depends on Solr) |
