"""Aggregates all versioned API routers."""

from fastapi import APIRouter

from app.api.chat import router as chat_router

# Create main API router
api_router = APIRouter()

# Register chat router with the API router
api_router.include_router(chat_router)
