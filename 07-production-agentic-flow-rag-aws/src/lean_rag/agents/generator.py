"""Generator: writes a cited answer from the selected evidence only.

It receives nothing but the question and the evidence passages chosen for this request; it
has no search or corpus access. Output is a list of sentences with citations so validation
can check each claim deterministically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import Field

from lean_rag.agents.base import AgentDeps, AgentRun, StrictModel
from lean_rag.agents.citations import Sentence
from lean_rag.agents.prompts import GENERATOR_SYSTEM, fence_evidence, schema_hint
from lean_rag.domain.models import Chunk
from lean_rag.observability.logging import log_event
from lean_rag.security.injection import looks_like_injection
from lean_rag.textutil import coverage, sentences

logger = logging.getLogger(__name__)


class SentenceOut(StrictModel):
    text: str = Field(min_length=1, max_length=800)
    citations: list[str] = Field(default_factory=list, max_length=4)


class GeneratorOut(StrictModel):
    insufficient_evidence: bool
    sentences: list[SentenceOut] = Field(default_factory=list, max_length=12)


@dataclass(frozen=True)
class Draft:
    sentences: list[Sentence]
    insufficient: bool
    valid_output: bool  # False when the model's response could not be parsed


def build_prompt(question: str, evidence: dict[str, Chunk], feedback: str | None) -> str:
    parts = [
        f"Question:\n<untrusted_document>\n{question[:2000]}\n</untrusted_document>",
        "Evidence:",
        *(fence_evidence(label, c.title, c.text) for label, c in evidence.items()),
    ]
    if feedback:
        parts.append(f"Your previous answer failed validation: {feedback}. Fix it, or abstain.")
    parts.append(
        schema_hint(
            {
                "insufficient_evidence": False,
                "sentences": [{"text": "A factual sentence.", "citations": ["E1"]}],
            }
        )
    )
    return "\n\n".join(parts)


def extractive_answer(question: str, evidence: dict[str, Chunk], max_sentences: int = 3) -> Draft:
    """Deterministic baseline: pick the evidence sentences that best cover the question."""
    scored: list[tuple[float, int, str, str]] = []
    for order, (label, chunk) in enumerate(evidence.items()):
        for sent in sentences(chunk.text.replace("\n", " ")):
            if looks_like_injection(sent) or len(sent) > 600:
                continue  # never repeat injected instructions as if they were an answer
            score = coverage(question, sent)
            if score >= 0.34:
                scored.append((score, order, label, sent))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked: list[Sentence] = []
    seen: set[str] = set()
    for _, _, label, sent in scored:
        if sent not in seen:
            picked.append(Sentence(sent, (label,)))
            seen.add(sent)
        if len(picked) >= max_sentences:
            break
    return Draft(picked, insufficient=not picked, valid_output=True)


class Generator:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    def generate(
        self, question: str, evidence: dict[str, Chunk], run: AgentRun, feedback: str | None = None
    ) -> Draft:
        if not self.deps.uses_llm:
            return extractive_answer(question, evidence)
        assert self.deps.gateway is not None
        out = self.deps.gateway.structured(
            agent="generator",
            system=GENERATOR_SYSTEM,
            prompt=build_prompt(question, evidence, feedback),
            schema=GeneratorOut,
            model_id=self.deps.settings.generator_model_id,
            budget=run.budget,
        )
        if out is None:
            log_event(logger, "generator_invalid_output", logging.WARNING)
            return Draft([], insufficient=False, valid_output=False)
        sents = [Sentence(s.text.strip(), tuple(dict.fromkeys(s.citations))) for s in out.sentences]
        return Draft(sents, out.insufficient_evidence, valid_output=True)
