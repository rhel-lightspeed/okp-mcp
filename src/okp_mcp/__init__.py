"""OKP MCP server — SOLR bridge for Red Hat knowledge base search."""

import logging

from pydantic_settings import CliApp

from okp_mcp import server as _server
from okp_mcp import tools as _tools  # noqa: F401 — import triggers @mcp.tool registration
from okp_mcp.config import ServerConfig
from okp_mcp.request_id import RequestIDLogFilter, build_http_request_id_middleware
from okp_mcp.server import mcp

__all__ = ["mcp", "main"]

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    """Configure logging at the given level."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s",
        force=True,
    )

    request_id_filter = RequestIDLogFilter()
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(request_id_filter)


def main() -> None:
    """Run the MCP server with configurable transport.

    Settings are loaded from CLI arguments and MCP_* environment variables.
    CLI arguments take precedence over environment variables.
    Run ``okp-mcp --help`` for available options.
    """
    config = CliApp.run(ServerConfig)
    _server._server_config = config
    _configure_logging(config.log_level)

    logger.info("Starting MCP server with transport=%s", config.transport)

    if config.transport in ("sse", "streamable-http"):
        mcp.run(
            transport=config.transport,
            host=config.host,
            port=config.port,
            middleware=build_http_request_id_middleware(),
        )
    else:
        mcp.run(transport="stdio")
