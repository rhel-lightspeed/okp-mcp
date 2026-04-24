"""Server configuration via MCP_* environment variables and CLI arguments."""

import logging
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
        ge=1,
        description="Maximum characters in a single tool response",
    )
    glitchtip_dsn: SecretStr | None = Field(
        default=None,
        description="GlitchTip/Sentry DSN for exception reporting",
    )

    @computed_field
    @property
    def solr_endpoint(self) -> str:
        """Solr select endpoint derived from solr_url."""
        return f"{self.solr_url}/solr/portal/select"
