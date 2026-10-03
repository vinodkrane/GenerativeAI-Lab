"""Add a unique ID to every request for tracking."""

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:

        # Use existing ID or create a new one
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Save ID for this request
        request.state.request_id = request_id

        # Clear previous request context to avoid leaking data between requests
        structlog.contextvars.clear_contextvars()

        # Add the current request ID to all logs for this request
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Continue request
        response = await call_next(request)

        # Return ID to caller
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
