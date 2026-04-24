"""Error reporting setup for GlitchTip-compatible Sentry DSNs."""

import logging
from typing import TYPE_CHECKING, Any

import sentry_sdk
from sentry_sdk.utils import BadDsn

from okp_mcp.build_info import get_commit_sha, get_package_version
from okp_mcp.config import ServerConfig

if TYPE_CHECKING:
    from sentry_sdk._types import Event

logger = logging.getLogger(__name__)

# Paths whose errors are monitoring noise, not actionable incidents.
_EXCLUDED_PATHS: frozenset[str] = frozenset({"/metrics"})


def _before_send(event: "Event", hint: dict[str, Any]) -> "Event | None":
    """Drop error events originating from health-check or metrics paths.

    Filters two categories of noise:
    * HTTP errors on ``/metrics`` (scraper hiccups, not real faults).
    * ``ConnectionResetError`` anywhere (TCP health probes disconnecting).
    """
    # Drop errors from excluded paths (metrics scraping, health checks)
    request_info = event.get("request", {})
    url = str(request_info.get("url", "")) if isinstance(request_info, dict) else ""
    if any(url.endswith(path) for path in _EXCLUDED_PATHS):
        return None

    # Drop connection resets (typically from liveness/readiness probe disconnects)
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type = exc_info[0]
        if exc_type is ConnectionResetError:
            return None

    return event


def initialize_error_reporting(config: ServerConfig) -> None:
    """Configure GlitchTip/Sentry exception reporting when a DSN is available."""
    if config.glitchtip_dsn is None:
        logger.debug("GlitchTip DSN is not configured; exception reporting is disabled")
        return

    glitchtip_dsn = config.glitchtip_dsn.get_secret_value()
    if not glitchtip_dsn:
        logger.debug("GlitchTip DSN is not configured; exception reporting is disabled")
        return

    release = f"okp-mcp@{get_package_version()}+{get_commit_sha()}"
    logger.info("GlitchTip DSN found; enabling exception reporting")
    try:
        sentry_sdk.init(
            dsn=glitchtip_dsn,
            release=release,
            send_default_pii=False,
            traces_sample_rate=0.0,
            before_send=_before_send,
        )
    except BadDsn:
        logger.warning("GlitchTip DSN is invalid; exception reporting is disabled", exc_info=True)
