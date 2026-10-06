"""Deterministic chunking. The Chunking & Enrichment Agent picks a ``ChunkPlan``; this module
executes it. Bounds are clamped here, so a bad plan cannot produce pathological chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from lean_rag.config import CHUNKER_VERSION
from lean_rag.domain.models import Chunk, Document, chunk_id_for
from lean_rag.reliability import PermanentError
from lean_rag.security.injection import looks_like_injection
from lean_rag.textutil import sentences, top_keywords

MIN_CHARS, MAX_CHARS = 300, 3_000
MAX_OVERLAP = 400


class Strategy(StrEnum):
    HEADING = "heading"  # documents with markdown/HTML headings
    PARAGRAPH = "paragraph"  # prose with clear paragraph breaks
    FIXED = "fixed"  # sentence-window fallback for flat text (e.g. OCR output)


@dataclass(frozen=True)
class ChunkPlan:
    strategy: Strategy
    target_chars: int = 1_200
    overlap_chars: int = 150

    def clamped(self) -> ChunkPlan:
        target = max(MIN_CHARS, min(MAX_CHARS, self.target_chars))
        overlap = max(0, min(MAX_OVERLAP, self.overlap_chars, target // 3))
        return ChunkPlan(self.strategy, target, overlap)


_HEADING = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split on markdown-style headings (HTML headings were converted during parsing)."""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(None, text)]
    sections: list[tuple[str | None, str]] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        sections.append((None, text[: matches[0].start()].strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        if body:
            sections.append((m.group(2).strip(), body))
    return sections


def _units(text: str, target: int) -> list[str]:
    """Paragraphs, with oversized paragraphs broken into sentences and hard-split if needed."""
    units: list[str] = []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if len(para) <= target:
            units.append(para)
            continue
        for sentence in sentences(para) or [para]:
            while len(sentence) > target:
                units.append(sentence[:target])
                sentence = sentence[target:]
            if sentence:
                units.append(sentence)
    return units


def _pack(units: list[str], target: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for unit in units:
        if current and size + len(unit) + 1 > target:
            chunks.append("\n\n".join(current))
            # carry trailing units forward as overlap
            carry: list[str] = []
            carried = 0
            for prev in reversed(current):
                if carried + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                carried += len(prev)
            current, size = carry, carried
        current.append(unit)
        size += len(unit) + 1
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _fixed_windows(text: str, target: int, overlap: int) -> list[str]:
    flat = " ".join(text.split())
    units = sentences(flat) or [flat]
    return _pack([u for s in units for u in _units(s, target)], target, overlap)


def chunk_text(text: str, plan: ChunkPlan) -> list[tuple[str | None, str]]:
    plan = plan.clamped()
    if plan.strategy is Strategy.FIXED:
        return [(None, c) for c in _fixed_windows(text, plan.target_chars, plan.overlap_chars)]
    sections = split_sections(text) if plan.strategy is Strategy.HEADING else [(None, text)]
    out: list[tuple[str | None, str]] = []
    for heading, body in sections:
        for piece in _pack(_units(body, plan.target_chars), plan.target_chars, plan.overlap_chars):
            out.append((heading, piece))
    return out


def build_chunks(
    doc: Document, text: str, plan: ChunkPlan, title: str | None, doc_keywords: list[str], max_chunks: int
) -> list[Chunk]:
    pieces = chunk_text(text, plan)
    if not pieces:
        raise PermanentError("chunking produced no chunks")
    if len(pieces) > max_chunks:
        raise PermanentError(f"document produced {len(pieces)} chunks (limit {max_chunks})")
    assert doc.content_hash
    principals = doc.acl_principals()
    chunks: list[Chunk] = []
    for ordinal, (heading, body) in enumerate(pieces):
        keywords = list(dict.fromkeys(top_keywords(body, 6) + doc_keywords[:4]))
        chunks.append(
            Chunk(
                chunk_id=chunk_id_for(doc.tenant_id, doc.document_id, doc.version, CHUNKER_VERSION, ordinal),
                document_id=doc.document_id,
                tenant_id=doc.tenant_id,
                document_version=doc.version,
                ordinal=ordinal,
                text=body,
                heading=heading,
                title=title,
                keywords=keywords,
                acl_principals=principals,
                content_hash=doc.content_hash,
                chunker_version=CHUNKER_VERSION,
                injection_suspect=looks_like_injection(body),
            )
        )
    return chunks
