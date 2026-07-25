"""ASGI middleware that stamps every request/response with X-Request-ID."""

from __future__ import annotations

from collections.abc import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .request_id import REQUEST_ID_HEADER, clear_request_id, ensure_request_id

log = structlog.get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = ensure_request_id(incoming)
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception:
            clear_request_id()
            raise

        response.headers[REQUEST_ID_HEADER] = request_id
        # CORS: browsers need to see custom headers when frontends read them.
        expose = response.headers.get("access-control-expose-headers", "")
        if REQUEST_ID_HEADER.lower() not in expose.lower():
            response.headers["Access-Control-Expose-Headers"] = (
                f"{expose}, {REQUEST_ID_HEADER}".strip(", ")
            )

        clear_request_id()
        return response
