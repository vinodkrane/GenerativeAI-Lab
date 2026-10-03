"""Request and response models used by the chat API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Role(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class HistoryMessage(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(
        ..., min_length=1, max_length=128, description="Conversation/session identifier"
    )
    user_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_rag: bool = Field(
        default=False, description="Whether to fetch RAG context for this turn"
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    message: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
