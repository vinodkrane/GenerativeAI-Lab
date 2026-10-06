"""FastAPI application: document upload/status endpoints and the query endpoint.

No `from __future__ import annotations` here: FastAPI resolves the `UserDep` annotation
defined inside `create_app` at runtime.
"""

import logging
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

import jwt
import sqlalchemy as sa
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status

from lean_rag.api.schemas import (
    CreateDocumentRequest,
    CreateDocumentResponse,
    DocumentView,
    NewVersionRequest,
    QueryRequest,
    UploadInstructions,
)
from lean_rag.config import get_settings
from lean_rag.container import Container, build_container
from lean_rag.domain.models import DocStatus, Document, QueryResult, User, derived_prefix
from lean_rag.domain.state_machine import can_transition
from lean_rag.ingestion.parsing import CONTENT_TYPES, kind_for_filename
from lean_rag.observability.logging import configure_logging, log_context, log_event
from lean_rag.reliability import PermanentError
from lean_rag.security.acl import AuthorizationError, can_manage_document, can_read_document, validate_grants
from lean_rag.security.auth import AuthError
from lean_rag.storage.objects import LocalObjectStore
from lean_rag.worker import Worker, s3_event

logger = logging.getLogger(__name__)


def create_app(container: Container) -> FastAPI:
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        thread: threading.Thread | None = None
        if container.settings.embedded_worker:
            worker = Worker(container, worker_id="embedded")
            worker.stop = stop
            thread = threading.Thread(target=worker.run_forever, name="embedded-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=container.settings.sqs_wait_time_s + 5)

    app = FastAPI(title="Lean Agentic RAG", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    limits = container.settings.limits

    @app.middleware("http")
    async def correlation(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        with log_context(request_id=request_id[:64]):
            response = await call_next(request)
        response.headers["x-request-id"] = request_id[:64]
        return response

    def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        try:
            return container.authenticator.authenticate(authorization[7:].strip())
        except AuthError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    UserDep = Annotated[User, Depends(current_user)]

    def readable(user: User, document_id: str) -> Document:
        doc = container.repo.get(document_id)
        # 404 for both "missing" and "not yours": existence is not disclosed across ACLs.
        if doc is None or doc.status is DocStatus.DELETED or not can_read_document(user, doc):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
        return doc

    def presign(doc: Document) -> UploadInstructions:
        upload = container.objects.presign_upload(
            doc.object_key, doc.content_type, limits.max_upload_bytes, container.settings.presign_expiry_s
        )
        return UploadInstructions(
            url=upload.url,
            method=upload.method,
            fields=upload.fields,
            expires_in=upload.expires_in,
            max_bytes=limits.max_upload_bytes,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        with container.repo.engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return {"status": "ready"}

    @app.post("/documents", status_code=status.HTTP_201_CREATED)
    def create_document(body: CreateDocumentRequest, user: UserDep) -> CreateDocumentResponse:
        try:
            kind = kind_for_filename(body.filename)
            validate_grants(user, body.allowed_groups, body.allowed_users)
        except PermanentError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        except AuthorizationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        # The record exists before any bytes arrive, so every S3 event maps to a known document.
        doc = Document(
            document_id=uuid.uuid4().hex,
            tenant_id=user.tenant_id,
            owner_id=user.sub,
            filename=body.filename,
            content_type=CONTENT_TYPES[kind],
            allowed_groups=sorted(set(body.allowed_groups)),
            allowed_users=sorted(set(body.allowed_users)),
        )
        container.repo.create(doc)
        log_event(logger, "document_created", document_id=doc.document_id, tenant_id=doc.tenant_id)
        return CreateDocumentResponse(document=DocumentView.of(doc), upload=presign(doc))

    @app.post("/documents/{document_id}/versions")
    def new_version(document_id: str, body: NewVersionRequest, user: UserDep) -> CreateDocumentResponse:
        doc = readable(user, document_id)
        if not can_manage_document(user, doc):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner or an admin can upload versions")
        if not can_transition(doc.status, DocStatus.PENDING_UPLOAD):
            raise HTTPException(status.HTTP_409_CONFLICT, f"document is {doc.status}; wait for it to settle")
        filename = body.filename or doc.filename
        try:
            content_type = CONTENT_TYPES[kind_for_filename(filename)]
        except PermanentError as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
        ok = container.repo.transition(
            doc.document_id,
            doc.status,
            DocStatus.PENDING_UPLOAD,
            "new version requested",
            version=doc.version + 1,
            filename=filename,
            content_type=content_type,
            content_hash=None,
            error=None,
            duplicate_of=None,
            attempts=0,
            rechunk_attempts=0,
            probe_pass_rate=None,
        )
        if not ok:
            raise HTTPException(status.HTTP_409_CONFLICT, "document changed concurrently; retry")
        updated = readable(user, document_id)
        return CreateDocumentResponse(document=DocumentView.of(updated), upload=presign(updated))

    @app.get("/documents")
    def list_documents(user: UserDep) -> list[DocumentView]:
        docs = container.repo.list_for_tenant(user.tenant_id)
        return [DocumentView.of(d) for d in docs if can_read_document(user, d)]

    @app.get("/documents/{document_id}")
    def get_document(document_id: str, user: UserDep) -> DocumentView:
        return DocumentView.of(readable(user, document_id))

    @app.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_document(document_id: str, user: UserDep) -> Response:
        doc = readable(user, document_id)
        if not can_manage_document(user, doc):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner or an admin can delete")
        if not container.repo.transition(
            doc.document_id, doc.status, DocStatus.DELETED, f"deleted by {user.sub}"
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, f"cannot delete a document in state {doc.status}")
        if container.index.live_index():
            container.index.delete_document(doc.document_id)
        container.objects.delete_prefix(f"raw/{doc.tenant_id}/{doc.document_id}/")
        for version in range(1, doc.version + 1):
            container.objects.delete_prefix(derived_prefix(doc.tenant_id, doc.document_id, version))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/documents/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
    def reprocess(document_id: str, user: UserDep) -> DocumentView:
        doc = readable(user, document_id)
        if not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
        if not can_transition(doc.status, DocStatus.RECEIVED):
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"cannot reprocess a document in state {doc.status}"
            )
        container.repo.transition(
            doc.document_id, doc.status, DocStatus.RECEIVED, "operator reprocess", error=None
        )
        container.queue.send(
            {"type": "reprocess", "tenant_id": doc.tenant_id, "document_id": doc.document_id}
        )
        return DocumentView.of(readable(user, document_id))

    @app.post("/query")
    def query(body: QueryRequest, user: UserDep) -> QueryResult:
        return container.query.answer(user, body.question)

    if isinstance(container.objects, LocalObjectStore):
        store = container.objects

        @app.put("/local-upload/{token}", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
        async def local_upload(token: str, request: Request) -> Response:
            """Local stand-in for an S3 presigned upload + S3 event notification."""
            try:
                claims = store.verify_upload_token(token)
            except jwt.PyJWTError as exc:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid or expired upload token") from exc
            if request.headers.get("content-type", "").split(";")[0] != claims["ct"]:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "content type does not match the upload")
            data = await request.body()
            if not 0 < len(data) <= int(claims["max"]):
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "invalid upload size")
            store.put(claims["key"], data, claims["ct"])
            container.queue.send(s3_event(claims["key"], len(data)))
            return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def app_factory() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    return create_app(build_container(settings))


def main() -> None:
    import uvicorn

    configure_logging(get_settings().log_level)
    uvicorn.run(
        "lean_rag.api.app:app_factory",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container port, fronted by the ALB
        port=8000,
        proxy_headers=True,
        log_config=None,  # uvicorn logs go through the JSON root handler
    )


if __name__ == "__main__":
    main()
