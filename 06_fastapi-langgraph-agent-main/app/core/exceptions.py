"""Custom exception hierarchy + FastAPI exception handlers.

Application errors and FastAPI error handlers.

All custom application errors inherit from AppError.
FastAPI converts them into a standard JSON response.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)


# Base class for all application-raised errors
class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(message)


# Authentication failed
class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "authentication_error"


# User does not have permission
class AuthorizationError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "authorization_error"


# Too many requests
class RateLimitExceededError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "rate_limit_exceeded"


# User input failed safety checks
class InputGuardrailError(AppError):
    """Raised when the incoming user request fails a safety/policy check."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "input_guardrail_violation"


# LLM response failed safety checks
class OutputGuardrailError(AppError):
    """Raised when the LLM's final response fails a safety/policy check."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "output_guardrail_violation"


# External tool failed
class ToolExecutionError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "tool_execution_error"


# LLM provider failed
class LLMProviderError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "llm_provider_error"


# Standard error format returned to clients.
def _error_envelope(
    request_id: str | None, code: str, message: str, details: dict
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


# Attach all exception handlers to the FastAPI app instance.
def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.warning(
            "app_error",
            error_code=exc.error_code,
            message=exc.message,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(
                request_id, exc.error_code, exc.message, exc.details
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.info("validation_error", errors=exc.errors(), request_id=request_id)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_envelope(
                request_id,
                "validation_error",
                "Request failed validation.",
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)

        # Dynamically map standard status codes to error string snake_case codes
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            error_code = "not_found"
        elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
            error_code = "method_not_allowed"
        else:
            error_code = "http_error"

        log.info("http_error", status_code=exc.status_code, request_id=request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(
                request_id,
                error_code,
                str(exc.detail),
                {},
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.error(
            "unhandled_exception", error=str(exc), request_id=request_id, exc_info=True
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_envelope(
                request_id, "internal_error", "An unexpected error occurred.", {}
            ),
        )
