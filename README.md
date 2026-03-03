# okp-solr-rag-prototype

## Getting started

1. Pull the Offline Knowledge Portal with public RAG prototype image:

```
podman pull images.paas.redhat.com/offline-kbase/okp-rag-proto:nov20
```

NOTE: this requires a VPN connection

2. Run the OKP image:

```
podman run --rm -p 8080:8080 -d --name okp okp-rag-proto:nov20
```

3. Verify OKP image is running locally by opening this in your browser: http://127.0.0.1:8080/

## Example queries

Execute a symantic query:

```
uv run okp_query.py -s "how do I enbable remote desktop in gnome in rhel?"
```

Execute a hybrid query:

```
uv run okp_query.py -y "CVE-2023-46604"
```

Include debug output with `-d`:

```
uv run okp_query.py -d -s "how do I enbable remote desktop in gnome in rhel?"
```
