"""Server configuration via MCP_* environment variables and CLI arguments."""

import logging
import sys

from enum import StrEnum

from pydantic import computed_field
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from starlette.middleware import Middleware as StarletteMiddleware

from okp_mcp.metrics import PrometheusMiddleware
from okp_mcp.request_id import RequestIDHeaderMiddleware


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# English stopwords stripped client-side before sending queries to Solr.
# The core set is Lucene's StopAnalyzer list, shipped with Solr as
# lang/stopwords_en.txt (Apache License 2.0).  Common question words and
# function words are added because LLM-generated queries frequently start
# with "How do I...", "What is...", "Can I...", etc.
#
# We strip these client-side because the Solr deployment's text_general
# field type references an empty stopwords.txt, so StopFilterFactory is
# effectively a no-op for most queried fields (title, main_content,
# heading_h1/h2, content, portal_synopsis, allTitle).  Only
# text_en_splitting_tight (all_content) uses the real Lucene list.
STOP_WORDS: frozenset[str] = frozenset(
    {
        # Lucene StopAnalyzer core (lang/stopwords_en.txt)
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "is",
        "it",
        "no",
        "not",
        "of",
        "on",
        "or",
        "such",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "will",
        "with",
        # Common question and function words (frequent in LLM queries)
        "can",
        "could",
        "do",
        "does",
        "has",
        "have",
        "how",
        "i",
        "long",
        "may",
        "should",
        "using",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "would",
    }
)


class Transport(StrEnum):
    http = "http"
    sse = "sse"
    stdio = "stdio"
    streamable_http = "streamable-http"


class ServerConfig(BaseSettings):
    """MCP server settings from CLI arguments and MCP_* environment variables.

    Precedence (highest to lowest): CLI args > env vars > defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        cli_prog_name="okp-mcp",
        cli_hide_none_type=True,
        cli_kebab_case=True,
        # Only parse CLI args when running okp-mcp from the command line,
        # not when importing the module.
        cli_parse_args=sys.argv[0].endswith("okp-mcp"),
    )

    transport: Transport = Field(
        default=Transport.streamable_http,
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
        ge=1,
        description="Maximum characters in a single tool response",
    )
    glitchtip_dsn: SecretStr | None = Field(
        default=None,
        description="GlitchTip/Sentry DSN for exception reporting",
    )
    stateless_http: bool = Field(
        default=True,
        description="Run in stateless mode (new transport per request, no session tracking). "
        "Eliminates sticky session requirement when multiple clients share one endpoint. "
        "Only applies to streamable-http transport. Set to false to restore stateful sessions.",
    )

    @computed_field
    @property
    def solr_endpoint(self) -> str:
        """Solr select endpoint derived from solr_url."""
        return f"{self.solr_url}/solr/portal/select"

    @property
    def transport_kwargs(self) -> dict[str, str | int | bool | list[StarletteMiddleware]]:
        result: dict[str, str | int | bool | list[StarletteMiddleware]] = {}
        if self.transport in {Transport.streamable_http, Transport.sse}:
            result["host"] = self.host
            result["port"] = self.port
            result["middleware"] = [
                StarletteMiddleware(PrometheusMiddleware),
                StarletteMiddleware(RequestIDHeaderMiddleware),
            ]
        if self.transport == Transport.streamable_http and self.stateless_http:
            result["stateless_http"] = True

        return result


CONFIG = ServerConfig()
