"""Tests for the MCP server entry point and transport dispatch."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from okp_mcp.metrics import PrometheusMiddleware
from okp_mcp.request_id import RequestIDHeaderMiddleware


@pytest.fixture
def _mock_mcp_run():
    """Patch mcp.run for the duration of a test."""
    with patch("okp_mcp.mcp") as mock_mcp:
        mock_mcp.run = MagicMock()
        yield mock_mcp


def _assert_http_run(mock_mcp, transport: str, host: str, port: int) -> None:
    """HTTP transports include Prometheus and request ID response middleware."""
    mock_mcp.run.assert_called_once()
    _, kwargs = mock_mcp.run.call_args

    assert kwargs["transport"] == transport
    assert kwargs["host"] == host
    assert kwargs["port"] == port
    assert len(kwargs["middleware"]) == 2
    middleware_classes = [m.cls for m in kwargs["middleware"]]
    assert middleware_classes[0] is PrometheusMiddleware
    assert middleware_classes[1] is RequestIDHeaderMiddleware


def test_module_imports():
    """Top-level package imports without errors."""
    import okp_mcp  # noqa: F401


def test_mcp_object_exists():
    """Package exposes a non-None mcp instance."""
    from okp_mcp import mcp

    assert mcp is not None


@pytest.mark.parametrize("name", ["mcp", "main"])
def test_public_api(name):
    """__all__ advertises the expected public symbols."""
    import okp_mcp

    assert name in okp_mcp.__all__


def test_main_defaults_to_streamable_http(_mock_mcp_run):
    """Default transport is streamable-http when no args or env vars are set."""
    from okp_mcp import main

    with patch("sys.argv", ["okp-mcp"]):
        main()

    _assert_http_run(_mock_mcp_run, transport="streamable-http", host="0.0.0.0", port=8000)


def test_main_stdio_transport(_mock_mcp_run):
    """stdio transport calls mcp.run without host/port."""
    from okp_mcp import main

    with patch("sys.argv", ["okp-mcp", "--transport", "stdio"]):
        main()

    _mock_mcp_run.run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_main_http_transports(_mock_mcp_run, transport):
    """SSE and streamable-http transports pass host and port to mcp.run."""
    from okp_mcp import main

    with patch("sys.argv", ["okp-mcp", "--transport", transport]):
        main()

    _assert_http_run(_mock_mcp_run, transport=transport, host="0.0.0.0", port=8000)


def test_main_custom_host_and_port(_mock_mcp_run):
    """CLI host and port are forwarded to mcp.run."""
    from okp_mcp import main

    with patch("sys.argv", ["okp-mcp", "--transport", "sse", "--host", "127.0.0.1", "--port", "3000"]):
        main()

    _assert_http_run(_mock_mcp_run, transport="sse", host="127.0.0.1", port=3000)


def test_main_env_var_transport(_mock_mcp_run):
    """MCP_TRANSPORT env var selects the transport when no CLI arg is given."""
    from okp_mcp import main

    with patch("sys.argv", ["okp-mcp"]), patch.dict("os.environ", {"MCP_TRANSPORT": "sse"}):
        main()

    _assert_http_run(_mock_mcp_run, transport="sse", host="0.0.0.0", port=8000)


def test_main_cli_overrides_env_var(_mock_mcp_run):
    """CLI arguments take precedence over environment variables."""
    from okp_mcp import main

    with (
        patch("sys.argv", ["okp-mcp", "--transport", "sse", "--port", "7777"]),
        patch.dict("os.environ", {"MCP_TRANSPORT": "streamable-http", "MCP_PORT": "9999"}),
    ):
        main()

    _assert_http_run(_mock_mcp_run, transport="sse", host="0.0.0.0", port=7777)


def test_main_invalid_transport_from_env():
    """Invalid transport from env var raises ValidationError."""
    from okp_mcp import main

    with (
        patch("sys.argv", ["okp-mcp"]),
        patch.dict("os.environ", {"MCP_TRANSPORT": "websocket"}),
        pytest.raises(ValidationError, match="transport"),
    ):
        main()
