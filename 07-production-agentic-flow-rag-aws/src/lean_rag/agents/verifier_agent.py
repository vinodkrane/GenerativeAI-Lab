"""Index Verifier Agent.

Judgment the agent provides: (1) realistic probe queries for sampled chunks and (2) when
probes fail, whether re-chunking is likely to help or a human should look. Deterministic
code samples the chunks, runs the probes through the real retrieval path, computes the pass
rate, and enforces the re-chunk bound.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from lean_rag.agents.base import AgentDeps, AgentRun, StrictModel
from lean_rag.agents.prompts import VERIFIER_SYSTEM, fence_evidence, schema_hint
from lean_rag.domain.models import Chunk
from lean_rag.observability.logging import log_event
from lean_rag.textutil import content_terms, top_keywords

logger = logging.getLogger(__name__)


class ProbeOut(StrictModel):
    chunk_label: str = Field(max_length=8)
    query: str = Field(min_length=3, max_length=200)


class ProbesOut(StrictModel):
    probes: list[ProbeOut] = Field(max_length=20)


class Recommendation(StrictModel):
    action: Literal["rechunk", "review"]
    reason: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class Probe:
    chunk_id: str
    query: str


def sample_chunks(chunks: list[Chunk], n: int) -> list[Chunk]:
    """Deterministic, evenly spaced sample - stable across retries."""
    if len(chunks) <= n:
        return list(chunks)
    step = len(chunks) / n
    return [chunks[int(i * step)] for i in range(n)]


def heuristic_probe(chunk: Chunk) -> str:
    keywords = top_keywords(chunk.text, 5)
    heading = " ".join(content_terms(chunk.heading or ""))[:60]
    return " ".join(filter(None, [heading, *keywords])) or chunk.text[:80]


class VerifierAgent:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    def write_probes(self, sample: list[Chunk], run: AgentRun) -> list[Probe]:
        fallback = [Probe(c.chunk_id, heuristic_probe(c)) for c in sample]
        if not self.deps.uses_llm or not sample:
            return fallback
        assert self.deps.gateway is not None
        labels = {f"C{i + 1}": c for i, c in enumerate(sample)}
        prompt = "\n\n".join(
            [
                "Sample chunks:",
                *(fence_evidence(label, c.heading or c.title, c.text[:1500]) for label, c in labels.items()),
                "Write one realistic user search query per chunk (8-15 words, no quotes).",
                schema_hint({"probes": [{"chunk_label": "C1", "query": "..."}]}),
            ]
        )
        out = self.deps.gateway.structured(
            agent="verifier",
            system=VERIFIER_SYSTEM,
            prompt=prompt,
            schema=ProbesOut,
            model_id=self.deps.settings.agent_model_id,
            budget=run.budget,
            max_tokens=600,
        )
        if out is None:
            log_event(logger, "verifier_agent_fallback", logging.WARNING, reason="invalid_output")
            return fallback
        probes = {p.chunk_label: p.query.strip() for p in out.probes if p.chunk_label in labels}
        # Any chunk the model skipped still gets a deterministic probe.
        return [Probe(c.chunk_id, probes.get(label) or heuristic_probe(c)) for label, c in labels.items()]

    def recommend(
        self, failed: list[tuple[Probe, Chunk]], pass_rate: float, can_rechunk: bool, run: AgentRun
    ) -> Recommendation:
        heuristic = Recommendation(
            action="rechunk" if can_rechunk else "review",
            reason=f"probe pass rate {pass_rate:.2f} below threshold",
        )
        if not can_rechunk or not self.deps.uses_llm:
            return heuristic  # the bound is enforced in code regardless of the model's view
        assert self.deps.gateway is not None
        prompt = "\n\n".join(
            [
                f"Probe pass rate: {pass_rate:.2f}. Failed probes:",
                *(
                    f"Query: {p.query}\n" + fence_evidence(f"F{i + 1}", c.heading, c.text[:800])
                    for i, (p, c) in enumerate(failed[:5])
                ),
                "Recommend 'rechunk' if chunk boundaries look like the problem (chunks mixing topics, "
                "too long, split mid-thought); 'review' if the content itself is unusable (garbled OCR, "
                "boilerplate, empty tables).",
                schema_hint({"action": "rechunk|review", "reason": "..."}),
            ]
        )
        out = self.deps.gateway.structured(
            agent="verifier",
            system=VERIFIER_SYSTEM,
            prompt=prompt,
            schema=Recommendation,
            model_id=self.deps.settings.agent_model_id,
            budget=run.budget,
            max_tokens=200,
        )
        return out or heuristic
