"""Request ID propagation helpers for FastMCP and Starlette."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_request_id_var: ContextVar[str | None] = ContextVar("okp_request_id", default=None)


def get_request_id() -> str | None:
    """Return the active request ID from task-local context."""
    return _request_id_var.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Store a request ID in task-local context."""
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous task-local request ID."""
    _request_id_var.reset(token)


class RequestIDLogFilter:
    """Attach a request_id field to every log record."""

    def filter(self, record: Any) -> bool:
        """Populate the formatter field even outside request handling."""
        record.request_id = get_request_id() or "-"
        return True


class RequestIDContextMiddleware(Middleware):
    """Mirror FastMCP request IDs into local logging context."""

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Keep the canonical FastMCP request ID active during message handling."""
        request_id = self._resolve_request_id(context)
        token = set_request_id(request_id) if request_id else None
        if request_id:
            self._store_request_id_on_http_request(request_id)

        try:
            return await call_next(context)
        finally:
            if token is not None:
                reset_request_id(token)

    def _resolve_request_id(self, context: MiddlewareContext[Any]) -> str | None:
        """Prefer HTTP-level correlation ID, then fall back to FastMCP's JSON-RPC id.

        RequestIDHeaderMiddleware sets a UUID in the contextvar for each HTTP
        request.  FastMCP's ``context.fastmcp_context.request_id`` is the
        JSON-RPC message ``id`` (typically a sequential integer like 1, 2, 3)
        which is useless for log correlation.  Keep the UUID when it exists;
        fall back to the JSON-RPC id only for non-HTTP transports (e.g. stdio).
        """
        existing = get_request_id()
        if existing is not None:
            return existing

        if context.fastmcp_context is not None and context.fastmcp_context.request_context is not None:
            return context.fastmcp_context.request_id

        try:
            http_request = get_http_request()
        except RuntimeError:
            return None

        return getattr(http_request.state, "request_id", None) or http_request.headers.get(REQUEST_ID_HEADER)

    def _store_request_id_on_http_request(self, request_id: str) -> None:
        """Persist the canonical request ID for response header middleware."""
        try:
            http_request = get_http_request()
        except RuntimeError:
            return

        http_request.state.request_id = request_id


class RequestIDHeaderMiddleware:
    """Expose the active request ID in HTTP and SSE response headers."""

    def __init__(self, app: ASGIApp):
        """Wrap the downstream ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Set a fallback request ID and stamp the final response headers."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        default_request_id = Headers(scope=scope).get(REQUEST_ID_HEADER) or uuid4().hex
        token = set_request_id(default_request_id)
        scope.setdefault("state", {})["request_id"] = default_request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                state = scope.get("state", {})
                request_id = state.get("request_id") or get_request_id() or default_request_id
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


def build_http_request_id_middleware() -> list[StarletteMiddleware]:
    """Return the Starlette middleware objects used for HTTP transports."""
    return [StarletteMiddleware(RequestIDHeaderMiddleware)]
