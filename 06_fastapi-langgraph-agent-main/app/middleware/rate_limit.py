"""Rate limiting via slowapi (a Flask-limiter-style wrapper for Starlette).

Limits requests based on the logged-in user when available.
Uses client IP address if no user information is found.
"""

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError, _error_envelope


# Returns a unique key for rate limiting using user ID or client IP
def _rate_limit_key(request: Request) -> str:

    # Get authenticated user details if available
    principal = getattr(request.state, "principal", None)

    # Apply limit based on user identity
    if principal and principal.get("subject"):
        return f"user:{principal['subject']}"

    # Use client IP address when user is not available
    return get_remote_address(request)


# Create rate limiter with default request limit
limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)


# Handle requests that exceed the rate limit
def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)

    # Create custom application error
    error = RateLimitExceededError(message=f"Rate limit exceeded: {exc.detail}")

    return JSONResponse(
        status_code=error.status_code,
        content=_error_envelope(
            request_id,
            error.error_code,
            error.message,
            error.details,
        ),
    )
