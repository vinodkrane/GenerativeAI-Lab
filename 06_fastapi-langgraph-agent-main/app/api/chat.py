"""Chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import handle_chat_turn

# Create router for chat-related APIs
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    # Rate limiting is handled globally by SlowAPIMiddleware.
    # Use @limiter.limit("N/minute") for route-specific limits.

    """Run one conversational turn through the full pipeline:

    validation -> state lookup -> prompt build -> guardrails -> LLM ->
    tool orchestration -> output guardrails -> typed response.
    """

    # Get request ID for tracking and logging
    request_id = getattr(request.state, "request_id", None)

    # Process the chat request and return the response
    return await handle_chat_turn(payload, request_id=request_id)
