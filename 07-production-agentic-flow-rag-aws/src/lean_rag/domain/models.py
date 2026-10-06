"""Domain models shared by the API, supervisors, agents and storage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    RECEIVED = "RECEIVED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    VERIFYING = "VERIFYING"
    INDEXED = "INDEXED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"
    DELETED = "DELETED"


class Document(BaseModel):
    """Workflow + metadata record for one uploaded document (one row in PostgreSQL)."""

    document_id: str
    tenant_id: str
    owner_id: str
    filename: str
    content_type: str
    version: int = 1
    status: DocStatus = DocStatus.PENDING_UPLOAD
    allowed_groups: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    size_bytes: int | None = None
    title: str | None = None
    chunk_count: int = 0
    attempts: int = 0
    rechunk_attempts: int = 0
    probe_pass_rate: float | None = None
    error: str | None = None
    duplicate_of: str | None = None
    chunker_version: str | None = None
    embedding_model: str | None = None
    index_version: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def object_key(self) -> str:
        return raw_object_key(self.tenant_id, self.document_id, self.version, self.filename)

    def acl_principals(self) -> list[str]:
        """Principals allowed to read this document. The owner is always included."""
        principals = {f"user:{self.owner_id}"}
        principals.update(f"user:{u}" for u in self.allowed_users)
        principals.update(f"group:{g}" for g in self.allowed_groups)
        return sorted(principals)


# Raw uploads and derived artifacts live under separate top-level prefixes so the S3 event
# notification can be filtered to "raw/" and the worker never sees its own writes.
def raw_object_key(tenant_id: str, document_id: str, version: int, filename: str) -> str:
    return f"raw/{tenant_id}/{document_id}/v{version}/{filename}"


def derived_prefix(tenant_id: str, document_id: str, version: int) -> str:
    return f"derived/{tenant_id}/{document_id}/v{version}/"


class Chunk(BaseModel):
    """One retrievable unit. Everything needed for ACL filtering travels with the chunk."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    tenant_id: str
    document_version: int
    ordinal: int
    text: str
    heading: str | None = None
    title: str | None = None
    keywords: list[str] = Field(default_factory=list)
    acl_principals: list[str]
    content_hash: str
    chunker_version: str
    embedding_model: str | None = None
    injection_suspect: bool = False
    embedding: list[float] | None = None


def chunk_id_for(tenant_id: str, document_id: str, version: int, chunker_version: str, ordinal: int) -> str:
    """Deterministic chunk id: re-running the same ingestion overwrites rather than duplicates."""
    raw = f"{tenant_id}|{document_id}|{version}|{chunker_version}|{ordinal}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class User(BaseModel):
    """An authenticated caller. Built only from a verified token, never from request bodies."""

    model_config = ConfigDict(frozen=True)

    sub: str
    tenant_id: str
    groups: tuple[str, ...] = ()

    @property
    def principals(self) -> tuple[str, ...]:
        return (f"user:{self.sub}", *(f"group:{g}" for g in self.groups))

    @property
    def is_admin(self) -> bool:
        return "admin" in self.groups


class SearchHit(BaseModel):
    chunk: Chunk
    score: float
    lexical_rank: int | None = None
    vector_rank: int | None = None


class Citation(BaseModel):
    evidence_id: str
    chunk_id: str
    document_id: str
    title: str | None
    snippet: str


class QueryResult(BaseModel):
    query_id: str
    answer: str
    abstained: bool
    reason: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: float = 0.0
