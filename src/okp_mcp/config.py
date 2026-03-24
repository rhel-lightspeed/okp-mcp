"""Server configuration via MCP_* environment variables and CLI arguments."""

import logging
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from stop_words import get_stop_words

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STOP_WORDS: frozenset[str] = frozenset(get_stop_words("en"))


class ServerConfig(BaseSettings):
    """MCP server settings from CLI arguments and MCP_* environment variables.

    Precedence (highest to lowest): CLI args > env vars > defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        cli_prog_name="okp-mcp",
        cli_hide_none_type=True,
    )

    transport: Literal["stdio", "sse", "streamable-http"] = Field(
        default="streamable-http",
        description="Transport protocol",
    )
    host: str = Field(
        default="0.0.0.0",  # noqa: S104 — intentional for container networking
        description="Host to bind to for HTTP transports",
    )
    port: int = Field(
        default=8000,
        description="Port to bind to for HTTP transports",
    )
    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    solr_url: str = Field(
        default="http://localhost:8983",
        description="Base URL of the Solr instance",
    )
    max_response_chars: int = Field(
        default=30_000,
        description="Maximum characters in a single tool response",
    )

    @computed_field
    @property
    def solr_endpoint(self) -> str:
        """Solr select endpoint derived from solr_url."""
        return f"{self.solr_url}/solr/portal/select"
