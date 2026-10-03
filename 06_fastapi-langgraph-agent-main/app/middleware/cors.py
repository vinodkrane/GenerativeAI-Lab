"""CORS configuration.

Kept as a small factory returning the kwargs for Starlette's built-in
CORSMiddleware, so app.main just does:
    app.add_middleware(CORSMiddleware, **cors_kwargs())
"""

from app.core.config import settings


def cors_kwargs() -> dict:
    return {
        "allow_origins": settings.cors_origins_list,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-ID"],
    }
