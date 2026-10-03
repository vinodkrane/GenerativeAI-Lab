"""Async Postgres access via SQLAlchemy 2.0 + asyncpg.

Creates the database connection, session factory, and stores
conversation messages permanently in the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import structlog
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

log = structlog.get_logger(__name__)


# Store the database engine and session factory for reuse.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# Base class used by all database tables.
class Base(DeclarativeBase):
    pass


# Store each conversation message permanently in the database for auditing and analysis.
class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    # Unique ID for each conversation record.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID used to group messages from the same conversation.
    session_id: Mapped[str] = mapped_column(String(128), index=True)

    # ID of the user who sent the message.
    user_id: Mapped[str] = mapped_column(String(128), index=True)

    # Message sender type, for example user or assistant.
    role: Mapped[str] = mapped_column(String(16))

    # Actual message content.
    content: Mapped[str] = mapped_column(Text)

    # Time when the message was created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )


# Create and connect the PostgreSQL database
async def connect_postgres() -> AsyncEngine:
    global _engine, _session_factory

    # Create the async database engine.
    _engine = create_async_engine(settings.postgres_url, pool_pre_ping=True, echo=False)

    # Create a factory for creating database sessions.
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    # Create database tables if they do not already exist.
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    log.info("postgres_connected")
    return _engine


# Close the PostgreSQL database connection
async def disconnect_postgres() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        log.info("postgres_disconnected")


# Create and return a database session
async def get_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Postgres not initialized - did the app lifespan run?")
    async with _session_factory() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Postgres not initialized - did the app lifespan run?")
    return _session_factory
