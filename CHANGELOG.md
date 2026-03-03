# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-03-02

### Added
- Initial repository setup for rls-backend
- OKP (Solr) RAG integration via MCP server
- Vertex AI backend configuration (Gemini 2.5 Flash)
- Local development setup with podman-compose
- Configuration files:
  - `run.yaml` - Llama Stack configuration (providers, models, storage)
  - `lightspeed-stack.yaml` - Service configuration (auth, inference)
  - `podman-compose.yaml` - Container orchestration
  - `.env.example` - Environment variable template
- Development utilities in `dev/` directory:
  - `nginx.conf` - Local auth header injection (simulates 3scale)
- Git submodule: `okp-solr-rag-providers` for OKP MCP server
- Documentation in README.md
- Version tracking:
  - `VERSION` file with current version
  - `RLS_BACKEND_VERSION` environment variable in containers for operations/debugging

### Fixed
- Healthcheck configurations to use CMD-SHELL format for docker-compose v2.34.0 compatibility
- All healthchecks now pass reliably (okp-mcp, llama-stack, lightspeed-stack)

[unreleased]: https://gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/rls-backend/compare/v0.1.0...HEAD
[0.1.0]: https://gitlab.cee.redhat.com/rhel-lightspeed/enhanced-shell/rls-backend/releases/tag/v0.1.0
