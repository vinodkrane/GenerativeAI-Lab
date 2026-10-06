"""Chunking & Enrichment Agent: chooses a chunking plan and document-level metadata."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import Field

from lean_rag.agents.base import AgentDeps, AgentRun, StrictModel
from lean_rag.agents.prompts import CHUNKING_SYSTEM, fence_document, schema_hint
from lean_rag.ingestion.chunking import ChunkPlan, Strategy, split_sections
from lean_rag.ingestion.parsing import ParsedDocument
from lean_rag.observability.logging import log_event
from lean_rag.textutil import top_keywords

logger = logging.getLogger(__name__)


class ChunkDecision(StrictModel):
    strategy: Literal["heading", "paragraph", "fixed"]
    target_chars: int = Field(ge=300, le=3000)
    overlap_chars: int = Field(ge=0, le=400)
    title: str | None = Field(default=None, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class Enrichment:
    plan: ChunkPlan
    title: str | None
    keywords: list[str]
    decided_by: str  # "llm" | "heuristic"


@dataclass(frozen=True)
class DocProfile:
    kind: str
    chars: int
    pages: int
    ocr_used: bool
    heading_sections: int
    paragraphs: int
    avg_paragraph_chars: int


def document_profile(parsed: ParsedDocument) -> DocProfile:
    paragraphs = [p for p in parsed.text.split("\n\n") if p.strip()]
    headings = sum(1 for heading, _ in split_sections(parsed.text) if heading)
    return DocProfile(
        kind=parsed.kind.value,
        chars=len(parsed.text),
        pages=parsed.pages,
        ocr_used=parsed.ocr_used,
        heading_sections=headings,
        paragraphs=len(paragraphs),
        avg_paragraph_chars=round(sum(map(len, paragraphs)) / max(1, len(paragraphs))),
    )


def heuristic_plan(parsed: ParsedDocument, previous: ChunkPlan | None) -> ChunkPlan:
    profile = document_profile(parsed)
    if previous is not None:
        # Re-chunk path: change strategy and shrink chunks so probes can match more precisely.
        fallback = {
            Strategy.HEADING: Strategy.PARAGRAPH,
            Strategy.PARAGRAPH: Strategy.FIXED,
            Strategy.FIXED: Strategy.FIXED,
        }
        return ChunkPlan(fallback[previous.strategy], int(previous.target_chars * 0.6), 100).clamped()
    if profile.heading_sections >= 2:
        return ChunkPlan(Strategy.HEADING, 1_200, 150)
    if profile.paragraphs >= 3 and not parsed.ocr_used:
        return ChunkPlan(Strategy.PARAGRAPH, 1_200, 150)
    return ChunkPlan(Strategy.FIXED, 1_000, 200)


class ChunkingAgent:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    def decide(
        self,
        parsed: ParsedDocument,
        run: AgentRun,
        previous: ChunkPlan | None = None,
        failure_note: str | None = None,
    ) -> Enrichment:
        fallback_plan = heuristic_plan(parsed, previous)
        fallback = Enrichment(fallback_plan, parsed.title, top_keywords(parsed.text, 8), "heuristic")
        if not self.deps.uses_llm:
            return fallback
        assert self.deps.gateway is not None
        prompt = "\n\n".join(
            [
                "Document profile:\n" + json.dumps(asdict(document_profile(parsed))),
                f"Previous plan that failed verification: {previous}" if previous else "",
                f"Verification notes: {failure_note}" if failure_note else "",
                "Opening of the document:\n" + fence_document(parsed.text, 4_000),
                "Choose strategy 'heading' for documents organised by headings, 'paragraph' for prose, "
                "'fixed' for flat or OCR text. Give a concise title (<= 12 words) and up to 8 keywords.",
                schema_hint(
                    {
                        "strategy": "heading|paragraph|fixed",
                        "target_chars": 1200,
                        "overlap_chars": 150,
                        "title": "...",
                        "keywords": ["..."],
                        "reason": "...",
                    }
                ),
            ]
        )
        decision = self.deps.gateway.structured(
            agent="chunking",
            system=CHUNKING_SYSTEM,
            prompt=prompt,
            schema=ChunkDecision,
            model_id=self.deps.settings.agent_model_id,
            budget=run.budget,
            max_tokens=400,
        )
        if decision is None:
            log_event(logger, "chunking_agent_fallback", logging.WARNING, reason="invalid_output")
            return fallback
        plan = ChunkPlan(Strategy(decision.strategy), decision.target_chars, decision.overlap_chars).clamped()
        keywords = [k.strip().lower() for k in decision.keywords if 0 < len(k.strip()) <= 40][:8]
        title = (decision.title or "").strip() or parsed.title
        return Enrichment(plan, title, keywords or fallback.keywords, "llm")
