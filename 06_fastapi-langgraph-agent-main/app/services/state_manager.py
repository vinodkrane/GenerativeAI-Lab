"""Conversation state management.

Short-term state (recent conversation turns used to build the prompt)
is stored in Redis using the session_id as the key for fast reads and writes.

Every message is also saved in Postgres (`ConversationLog`). This keeps
the conversation history even if Redis is cleared and allows it to be
used later for auditing or analysis.
"""

from __future__ import annotations

import json

import structlog
from sqlalchemy import select

from app.db.postgres import ConversationLog, session_factory
from app.db.redis import get_redis
from app.schemas.chat import HistoryMessage, Role

log = structlog.get_logger(__name__)

# Redis key prefix for chat history.
_HISTORY_KEY_PREFIX = "chat:history:"

# Keep only the latest 20 messages in Redis.
_MAX_HISTORY_MESSAGES = 20

# Remove chat history from Redis after 1 day.
_HISTORY_TTL_SECONDS = 60 * 60 * 24  # 1 day


# Build the Redis key for a chat session.
def _history_key(session_id: str) -> str:
    return f"{_HISTORY_KEY_PREFIX}{session_id}"


# Get recent chat history from Redis. This history is used to build the prompt for the LLM.
# If Redis is unavailable, return an empty list instead of failing the request.
async def get_history(session_id: str) -> list[HistoryMessage]:
    """Read recent conversation turns for prompt building.

    Falls back to an empty history (rather than raising) if Redis is
    unreachable, so a cache outage degrades to "no memory" instead of
    a hard failure.
    """
    try:
        redis_client = get_redis()
        raw_items = await redis_client.lrange(_history_key(session_id), 0, -1)
        return [HistoryMessage(**json.loads(item)) for item in raw_items]
    except Exception as exc:  # pragma: no cover - defensive degradation
        log.warning(
            "state_manager_history_read_failed", session_id=session_id, error=str(exc)
        )
        return []


# Save one chat message. The message is stored in Redis for fast access and in Postgres for permanent storage
async def append_turn(session_id: str, user_id: str, role: Role, content: str) -> None:
    """Append one message to both the short-term cache and the durable log."""
    message = HistoryMessage(role=role, content=content)

    try:
        redis_client = get_redis()
        key = _history_key(session_id)
        await redis_client.rpush(key, message.model_dump_json())
        await redis_client.ltrim(key, -_MAX_HISTORY_MESSAGES, -1)
        await redis_client.expire(key, _HISTORY_TTL_SECONDS)
    except Exception as exc:  # pragma: no cover - defensive degradation
        log.warning(
            "state_manager_history_write_failed", session_id=session_id, error=str(exc)
        )

    try:
        async with session_factory()() as db_session:
            db_session.add(
                ConversationLog(
                    session_id=session_id,
                    user_id=user_id,
                    role=role.value,
                    content=content,
                )
            )
            await db_session.commit()
    except Exception as exc:  # pragma: no cover - defensive degradation
        log.warning(
            "state_manager_durable_write_failed", session_id=session_id, error=str(exc)
        )


# Get the complete conversation from Postgres which is needed for debugging, auditing, or analytics.
async def get_full_history_from_db(session_id: str) -> list[HistoryMessage]:
    """Fetch the complete durable history for a session (audit / debugging)."""
    async with session_factory()() as db_session:
        stmt = (
            select(ConversationLog)
            .where(ConversationLog.session_id == session_id)
            .order_by(ConversationLog.created_at.asc())
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        return [
            HistoryMessage(role=Role(row.role), content=row.content) for row in rows
        ]
