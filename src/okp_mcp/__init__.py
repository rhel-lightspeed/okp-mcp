"""OKP MCP server — SOLR bridge for Red Hat knowledge base search."""

import logging
import sys

from pydantic_settings import CliApp

from okp_mcp import server as _server
from okp_mcp.build_info import get_commit_sha
from okp_mcp.build_info import get_package_version
from okp_mcp.config import ServerConfig
from okp_mcp.request_id import RequestIDLogFilter
from okp_mcp.server import mcp
from okp_mcp.telemetry import initialize_error_reporting


__all__ = ["mcp", "main"]

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    """Configure logging at the given level."""
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s [%(levelname)s] [request_id=%(request_id)s] %(name)s: %(message)s",
        force=True,
    )

    # httpx logs every HTTP request at INFO with the full URL-encoded query string.
    # Our solr.py already logs query params in readable form, so this is redundant noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)

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
    try:
        initialize_error_reporting(config)
    except Exception:
        logger.warning("Failed to initialize error reporting; continuing without it", exc_info=True)

    logger.info("okp-mcp %s (%s)", get_package_version(), get_commit_sha())
    logger.info("Starting MCP server with transport=%s", config.transport)

    try:
        mcp.run(transport=config.transport.value, show_banner=False, **config.transport_kwargs)
    except KeyboardInterrupt:
        logger.info("Shutting down okp-mcp")
        sys.exit()
    except Exception as exc:
        logger.critical(f"Fatal error in okp-mcp: {exc}", exc_info=True)
        sys.exit(1)
