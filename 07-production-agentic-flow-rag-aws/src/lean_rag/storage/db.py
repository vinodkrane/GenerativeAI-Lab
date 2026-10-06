"""Document and workflow state in PostgreSQL (SQLite for local dev/tests).

Status changes are compare-and-set updates guarded by the state machine, so concurrent or
duplicate workers cannot move a document backwards or skip a stage. A short lease stops two
workers processing the same document at the same time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from lean_rag.domain.models import DocStatus, Document, utcnow
from lean_rag.domain.state_machine import assert_transition

metadata = sa.MetaData()

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("document_id", sa.String(64), primary_key=True),
    sa.Column("tenant_id", sa.String(128), nullable=False, index=True),
    sa.Column("owner_id", sa.String(128), nullable=False),
    sa.Column("filename", sa.String(512), nullable=False),
    sa.Column("content_type", sa.String(128), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("status", sa.String(32), nullable=False, index=True),
    sa.Column("allowed_groups", sa.JSON, nullable=False),
    sa.Column("allowed_users", sa.JSON, nullable=False),
    sa.Column("content_hash", sa.String(64)),
    sa.Column("size_bytes", sa.BigInteger),
    sa.Column("title", sa.String(512)),
    sa.Column("chunk_count", sa.Integer, nullable=False, default=0),
    sa.Column("attempts", sa.Integer, nullable=False, default=0),
    sa.Column("rechunk_attempts", sa.Integer, nullable=False, default=0),
    sa.Column("probe_pass_rate", sa.Float),
    sa.Column("error", sa.Text),
    sa.Column("duplicate_of", sa.String(64)),
    sa.Column("chunker_version", sa.String(64)),
    sa.Column("embedding_model", sa.String(128)),
    sa.Column("index_version", sa.String(64)),
    sa.Column("lease_owner", sa.String(128)),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_documents_tenant_hash", "tenant_id", "content_hash"),
)

document_events = sa.Table(
    "document_events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("document_id", sa.String(64), nullable=False, index=True),
    sa.Column("from_status", sa.String(32)),
    sa.Column("to_status", sa.String(32), nullable=False),
    sa.Column("detail", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

index_versions = sa.Table(
    "index_versions",
    metadata,
    sa.Column("index_name", sa.String(128), primary_key=True),
    sa.Column("status", sa.String(32), nullable=False),  # BUILDING | READY | LIVE | RETIRED | REJECTED
    sa.Column("chunker_version", sa.String(64), nullable=False),
    sa.Column("embedding_model", sa.String(128), nullable=False),
    sa.Column("agent_policy_version", sa.String(64), nullable=False),
    sa.Column("eval_report", sa.JSON),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

_DOC_FIELDS = set(Document.model_fields)


def _aware(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def create_db_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        database = sa.engine.make_url(url).database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        return sa.create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    return sa.create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


class Repository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_schema(self) -> None:
        """Idempotent. API and worker tasks may start together; if two race on CREATE TABLE
        the loser retries once and finds the tables in place."""
        try:
            metadata.create_all(self.engine)
        except sa.exc.DBAPIError:
            metadata.create_all(self.engine)

    # --- documents ------------------------------------------------------------------------
    def _row_to_doc(self, row: Any) -> Document:
        data = {k: _aware(v) for k, v in row._mapping.items() if k in _DOC_FIELDS}
        return Document.model_validate(data)

    def create(self, doc: Document) -> None:
        with self.engine.begin() as conn:
            conn.execute(documents.insert().values(**doc.model_dump()))
            conn.execute(
                document_events.insert().values(
                    document_id=doc.document_id, from_status=None, to_status=doc.status, created_at=utcnow()
                )
            )

    def get(self, document_id: str) -> Document | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(documents).where(documents.c.document_id == document_id)).first()
        return self._row_to_doc(row) if row else None

    def list_for_tenant(self, tenant_id: str, limit: int = 100) -> list[Document]:
        q = (
            sa.select(documents)
            .where(documents.c.tenant_id == tenant_id, documents.c.status != DocStatus.DELETED)
            .order_by(documents.c.created_at.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [self._row_to_doc(r) for r in conn.execute(q)]

    def list_by_status(
        self, statuses: list[DocStatus], updated_after: datetime | None = None
    ) -> list[Document]:
        q = sa.select(documents).where(documents.c.status.in_([s.value for s in statuses]))
        if updated_after is not None:
            q = q.where(documents.c.updated_at > updated_after)
        with self.engine.connect() as conn:
            return [self._row_to_doc(r) for r in conn.execute(q.order_by(documents.c.created_at))]

    def find_indexed_duplicate(self, tenant_id: str, content_hash: str, exclude_id: str) -> Document | None:
        q = sa.select(documents).where(
            documents.c.tenant_id == tenant_id,
            documents.c.content_hash == content_hash,
            documents.c.document_id != exclude_id,
            documents.c.status == DocStatus.INDEXED,
        )
        with self.engine.connect() as conn:
            row = conn.execute(q.limit(1)).first()
        return self._row_to_doc(row) if row else None

    def transition(
        self,
        document_id: str,
        current: DocStatus,
        target: DocStatus,
        detail: str | None = None,
        **fields: Any,
    ) -> bool:
        """Compare-and-set status change. Returns False if another actor changed it first."""
        assert_transition(current, target)
        unknown = set(fields) - _DOC_FIELDS
        if unknown:
            raise ValueError(f"unknown document fields: {unknown}")
        now = utcnow()
        with self.engine.begin() as conn:
            result = conn.execute(
                documents.update()
                .where(documents.c.document_id == document_id, documents.c.status == current.value)
                .values(status=target.value, updated_at=now, **fields)
            )
            if result.rowcount != 1:
                return False
            conn.execute(
                document_events.insert().values(
                    document_id=document_id,
                    from_status=current.value,
                    to_status=target.value,
                    detail=detail,
                    created_at=now,
                )
            )
        return True

    def update_fields(self, document_id: str, **fields: Any) -> None:
        unknown = set(fields) - _DOC_FIELDS
        if unknown or "status" in fields:
            raise ValueError("status changes must go through transition()")
        with self.engine.begin() as conn:
            conn.execute(
                documents.update()
                .where(documents.c.document_id == document_id)
                .values(updated_at=utcnow(), **fields)
            )

    def events(self, document_id: str) -> list[tuple[str | None, str]]:
        q = (
            sa.select(document_events.c.from_status, document_events.c.to_status)
            .where(document_events.c.document_id == document_id)
            .order_by(document_events.c.id)
        )
        with self.engine.connect() as conn:
            return [(r[0], r[1]) for r in conn.execute(q)]

    # --- worker leases --------------------------------------------------------------------
    def claim(self, document_id: str, worker_id: str, lease_s: int) -> bool:
        now = utcnow()
        with self.engine.begin() as conn:
            result = conn.execute(
                documents.update()
                .where(
                    documents.c.document_id == document_id,
                    sa.or_(
                        documents.c.lease_owner.is_(None),
                        documents.c.lease_owner == worker_id,
                        documents.c.lease_expires_at < now,
                    ),
                )
                .values(lease_owner=worker_id, lease_expires_at=now + timedelta(seconds=lease_s))
            )
            return bool(result.rowcount == 1)

    def release(self, document_id: str, worker_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                documents.update()
                .where(documents.c.document_id == document_id, documents.c.lease_owner == worker_id)
                .values(lease_owner=None, lease_expires_at=None)
            )

    # --- index versions -------------------------------------------------------------------
    def upsert_index_version(self, index_name: str, status: str, **fields: Any) -> None:
        now = utcnow()
        with self.engine.begin() as conn:
            exists = conn.execute(
                sa.select(index_versions.c.index_name).where(index_versions.c.index_name == index_name)
            ).first()
            if exists:
                conn.execute(
                    index_versions.update()
                    .where(index_versions.c.index_name == index_name)
                    .values(status=status, updated_at=now, **fields)
                )
            else:
                conn.execute(
                    index_versions.insert().values(
                        index_name=index_name, status=status, created_at=now, updated_at=now, **fields
                    )
                )

    def list_index_versions(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(index_versions).order_by(index_versions.c.created_at))
            return [dict(r._mapping) for r in rows]
