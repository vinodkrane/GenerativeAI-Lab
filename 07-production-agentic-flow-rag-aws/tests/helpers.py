"""Test helpers: a scripted LLM client, a minimal PDF writer and an ingestion shortcut."""

from __future__ import annotations

from collections.abc import Callable

from lean_rag.container import Container
from lean_rag.domain.models import Document, User
from lean_rag.ingestion.parsing import CONTENT_TYPES, kind_for_filename
from lean_rag.llm.base import LLMResponse
from lean_rag.worker import Worker, s3_event

Handler = Callable[[str, str], str]  # (system, prompt) -> raw model text


class ScriptedLLM:
    """Test double for the Bedrock client. Routes by agent (from the system prompt)."""

    def __init__(self, **handlers: Handler | str) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def agent_for(system: str) -> str:
        for agent, marker in (
            ("chunking", "Chunking & Enrichment Agent"),
            ("verifier", "Index Verifier Agent"),
            ("retrieval", "Retrieval Agent"),
            ("generator", "answer generator"),
        ):
            if marker in system:
                return agent
        raise AssertionError("unknown agent prompt")

    def complete(self, *, system: str, prompt: str, model_id: str, max_tokens: int) -> LLMResponse:
        agent = self.agent_for(system)
        self.calls.append((agent, prompt))
        handler = self.handlers.get(agent)
        if handler is None:
            raise AssertionError(f"no scripted response for {agent}")
        text = handler(system, prompt) if callable(handler) else handler
        return LLMResponse(
            text=text, input_tokens=len(prompt) // 4, output_tokens=len(text) // 4, model_id=model_id
        )

    def count(self, agent: str) -> int:
        return sum(1 for a, _ in self.calls if a == agent)


def make_pdf(lines: list[str]) -> bytes:
    """A small, valid single-page PDF with real text objects (pypdf can extract it)."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    ops = ["BT", "/F1 11 Tf", "14 TL", "50 780 Td"] + [f"({t}) Tj T*" for t in escaped] + ["ET"]
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def create_document(
    c: Container,
    filename: str,
    owner: User,
    groups: list[str] | None = None,
    document_id: str | None = None,
) -> Document:
    doc = Document(
        document_id=document_id or f"doc-{len(c.repo.list_for_tenant(owner.tenant_id)) + 1}-{filename}",
        tenant_id=owner.tenant_id,
        owner_id=owner.sub,
        filename=filename,
        content_type=CONTENT_TYPES[kind_for_filename(filename)],
        allowed_groups=groups or [],
    )
    c.repo.create(doc)
    return doc


def upload(c: Container, doc: Document, data: bytes) -> None:
    c.objects.put(doc.object_key, data, doc.content_type)
    c.queue.send(s3_event(doc.object_key, len(data)))


def ingest(
    c: Container, filename: str, data: bytes, owner: User, groups: list[str] | None = None, **kw: str
) -> Document:
    doc = create_document(c, filename, owner, groups, kw.get("document_id"))
    upload(c, doc, data)
    Worker(c, worker_id="test").drain()
    stored = c.repo.get(doc.document_id)
    assert stored is not None
    return stored


POLICY_MD = b"""# Travel Policy

## Hotels

The nightly hotel limit is 180 GBP in London and 120 GBP elsewhere in the UK.

## Meals

The daily meal allowance is 45 GBP for domestic travel and 60 GBP for international travel.

## Claims

Expense claims must be submitted within 30 days of the end of the trip with itemised receipts.
"""

BOARD_TXT = b"""Board minutes.

The board approved the acquisition of Brightline Analytics for 42 million GBP.

The restructuring of the European sales organisation was discussed in detail.
"""

ALICE = User(sub="alice", tenant_id="acme", groups=("staff",))
BOB_BOARD = User(sub="bob", tenant_id="acme", groups=("board", "staff"))
ADMIN = User(sub="root", tenant_id="acme", groups=("admin",))
MALLORY = User(sub="mallory", tenant_id="globex", groups=("staff",))
