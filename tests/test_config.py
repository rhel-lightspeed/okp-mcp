"""Tests for ServerConfig settings loading and validation."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from pydantic_settings import CliApp

from okp_mcp.config import ServerConfig


def test_defaults():
    """All fields have sensible defaults when no env vars or CLI args are set."""
    config = ServerConfig()

    assert config.transport == "streamable-http"
    assert config.host == "0.0.0.0"
    assert config.port == 8000
    assert config.log_level == "INFO"
    assert config.solr_url == "http://localhost:8983"


@pytest.mark.parametrize(
    "transport",
    ["stdio", "sse", "streamable-http"],
)
def test_valid_transports(transport):
    """All supported transport values are accepted."""
    config = ServerConfig(transport=transport)

    assert config.transport == transport


def test_invalid_transport_rejected():
    """Transport values outside the Literal choices raise ValidationError."""
    with pytest.raises(ValidationError, match="transport"):
        ServerConfig(transport="websocket")


def test_env_var_loading():
    """Settings are populated from MCP_* environment variables."""
    with patch.dict(
        "os.environ",
        {"MCP_TRANSPORT": "sse", "MCP_HOST": "127.0.0.1", "MCP_PORT": "9090"},
    ):
        config = ServerConfig()

    assert config.transport == "sse"
    assert config.host == "127.0.0.1"
    assert config.port == 9090


def test_env_var_invalid_transport():
    """Invalid transport from env var raises ValidationError."""
    with (
        patch.dict("os.environ", {"MCP_TRANSPORT": "websocket"}),
        pytest.raises(ValidationError, match="transport"),
    ):
        ServerConfig()


def test_cli_args_via_cli_app():
    """CliApp.run parses CLI arguments into ServerConfig."""
    config = CliApp.run(
        ServerConfig,
        cli_args=["--transport", "sse", "--host", "127.0.0.1", "--port", "3000"],
    )

    assert config.transport == "sse"
    assert config.host == "127.0.0.1"
    assert config.port == 3000


def test_cli_overrides_env_var():
    """CLI arguments take precedence over environment variables."""
    with patch.dict("os.environ", {"MCP_TRANSPORT": "streamable-http"}):
        config = CliApp.run(
            ServerConfig,
            cli_args=["--transport", "sse"],
        )

    assert config.transport == "sse"


def test_env_var_overrides_default():
    """Environment variables take precedence over field defaults."""
    with patch.dict("os.environ", {"MCP_PORT": "9999"}):
        config = ServerConfig()

    assert config.port == 9999


def test_port_type_coercion():
    """Port value from env var string is coerced to int."""
    with patch.dict("os.environ", {"MCP_PORT": "4000"}):
        config = ServerConfig()

    assert config.port == 4000
    assert isinstance(config.port, int)


@pytest.mark.parametrize(
    "solr_url, expected_endpoint",
    [
        ("http://localhost:8983", "http://localhost:8983/solr/portal/select"),
        ("http://rhel-okp:8983", "http://rhel-okp:8983/solr/portal/select"),
        ("http://custom:9999", "http://custom:9999/solr/portal/select"),
    ],
)
def test_solr_endpoint_derived_from_url(solr_url, expected_endpoint):
    """solr_endpoint computed field is derived from solr_url."""
    config = ServerConfig(solr_url=solr_url)

    assert config.solr_endpoint == expected_endpoint


def test_solr_url_from_env():
    """MCP_SOLR_URL env var populates solr_url and solr_endpoint."""
    with patch.dict("os.environ", {"MCP_SOLR_URL": "http://remote:1234"}):
        config = ServerConfig()

    assert config.solr_url == "http://remote:1234"
    assert config.solr_endpoint == "http://remote:1234/solr/portal/select"


def test_max_response_chars_default():
    """max_response_chars defaults to 30000."""
    config = ServerConfig()
    assert config.max_response_chars == 30_000


def test_max_response_chars_env_override():
    """MCP_MAX_RESPONSE_CHARS environment variable overrides the default."""
    with patch.dict("os.environ", {"MCP_MAX_RESPONSE_CHARS": "20000"}):
        config = ServerConfig()
    assert config.max_response_chars == 20_000


@pytest.mark.parametrize("bad_value", [0, -1, -100])
def test_max_response_chars_rejects_non_positive(bad_value):
    """max_response_chars rejects zero and negative values at load time."""
    with pytest.raises(ValidationError, match="max_response_chars"):
        ServerConfig(max_response_chars=bad_value)
