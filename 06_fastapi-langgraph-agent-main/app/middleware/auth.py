"""Authentication middleware.

Checks JWT or API key for incoming requests.
Allows public paths like health checks and docs without authentication.
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.exceptions import AuthenticationError
from app.core.security import resolve_identity

log = structlog.get_logger(__name__)

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:

        # Allow public paths and OPTIONS requests
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        try:
            # Check user identity from JWT token or API key
            principal = resolve_identity(
                authorization_header=request.headers.get("Authorization"),
                api_key_header=request.headers.get("X-API-Key"),
            )
        except AuthenticationError as exc:
            # Log failed authentication attempts
            log.warning("auth_failed", path=request.url.path, reason=exc.message)

            # Return authentication error response
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.error_code, "message": exc.message}},
            )

        # Store authenticated user details for later use
        request.state.principal = principal

        # Continue request processing
        return await call_next(request)
