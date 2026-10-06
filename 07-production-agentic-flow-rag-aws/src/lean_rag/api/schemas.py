"""HTTP request/response models. Note: no request model accepts a tenant or owner - those
come only from the verified token."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from lean_rag.domain.models import DocStatus, Document


class CreateDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]*$")
    allowed_groups: list[str] = Field(default_factory=list, max_length=50)
    allowed_users: list[str] = Field(default_factory=list, max_length=200)


class NewVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str | None = Field(
        default=None, min_length=1, max_length=255, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]*$"
    )


class UploadInstructions(BaseModel):
    url: str
    method: str
    fields: dict[str, str]
    expires_in: int
    max_bytes: int


class DocumentView(BaseModel):
    document_id: str
    filename: str
    version: int
    status: DocStatus
    title: str | None
    chunk_count: int
    probe_pass_rate: float | None
    error: str | None
    duplicate_of: str | None
    owner_id: str
    allowed_groups: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, doc: Document) -> DocumentView:
        return cls.model_validate(doc.model_dump(include=set(cls.model_fields)))


class CreateDocumentResponse(BaseModel):
    document: DocumentView
    upload: UploadInstructions


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=2000)
