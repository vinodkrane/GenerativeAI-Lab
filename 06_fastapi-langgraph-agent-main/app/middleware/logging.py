"""Log each HTTP request with method, path, status, and duration."""

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Record the request start time.
        start = time.perf_counter()
        response: Response | None = None
        try:
            # Pass the request to the next middleware or route handler.
            response = await call_next(request)
            return response
        finally:
            # Log the request even if an exception occurs.
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code if response else 500,
                duration_ms=duration_ms,
            )
