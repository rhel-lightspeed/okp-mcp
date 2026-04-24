"""Tests for GlitchTip/Sentry error reporting setup."""

from unittest.mock import patch

import pytest
import sentry_sdk
from pydantic import SecretStr

from okp_mcp.config import ServerConfig
from okp_mcp.telemetry import _before_send, initialize_error_reporting


@pytest.mark.parametrize(
    "dsn_value",
    [
        pytest.param(None, id="missing"),
        pytest.param(SecretStr(""), id="empty"),
    ],
)
def test_initialize_error_reporting_skips_blank_or_missing_dsn(dsn_value):
    """Sentry is not initialized when the GlitchTip DSN is missing or empty."""
    config = ServerConfig(glitchtip_dsn=dsn_value)

    with patch("okp_mcp.telemetry.sentry_sdk.init") as sentry_init:
        initialize_error_reporting(config)

    sentry_init.assert_not_called()


def test_initialize_error_reporting_uses_configured_dsn():
    """Sentry receives the configured GlitchTip DSN with sampling and before_send hook."""
    config = ServerConfig(glitchtip_dsn=SecretStr("https://glitchtip.example.com/1"))

    with (
        patch("okp_mcp.telemetry.get_commit_sha", return_value="abc123"),
        patch("okp_mcp.telemetry.get_package_version", return_value="1.2.3"),
        patch("okp_mcp.telemetry.sentry_sdk.init") as sentry_init,
    ):
        initialize_error_reporting(config)

    sentry_init.assert_called_once_with(
        dsn="https://glitchtip.example.com/1",
        release="okp-mcp@1.2.3+abc123",
        send_default_pii=False,
        traces_sample_rate=0.0,
        before_send=_before_send,
    )


def test_initialize_error_reporting_handles_invalid_dsn(caplog):
    """Invalid GlitchTip DSNs do not block application startup."""
    config = ServerConfig(glitchtip_dsn=SecretStr("not-a-dsn"))

    initialize_error_reporting(config)

    assert "GlitchTip DSN is invalid" in caplog.text
    assert sentry_sdk.is_initialized() is False


def test_before_send_drops_metrics_path_errors():
    """Errors from /metrics requests are filtered out as monitoring noise."""
    event = {"request": {"url": "http://localhost:8000/metrics"}}
    assert _before_send(event, {}) is None


def test_before_send_drops_connection_reset_errors():
    """ConnectionResetError (health probe disconnects) is filtered out."""
    event = {"exception": {"values": [{"type": "ConnectionResetError"}]}}
    try:
        raise ConnectionResetError("peer disconnected")
    except ConnectionResetError:
        import sys

        hint = {"exc_info": sys.exc_info()}
    assert _before_send(event, hint) is None


def test_before_send_keeps_regular_errors():
    """Normal application errors pass through the filter unchanged."""
    event = {"request": {"url": "http://localhost:8000/mcp"}, "exception": {"values": [{"type": "ValueError"}]}}
    assert _before_send(event, {}) is event


def test_before_send_keeps_events_without_request():
    """Events with no request info are not filtered."""
    event = {"level": "error", "message": "something broke"}
    assert _before_send(event, {}) is event
