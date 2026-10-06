"""Retrieval Agent: plans queries and judges evidence sufficiency.

The agent's outputs are plain query strings and chunk labels. Its schemas have no field for
tenants, ACLs, indexes or tools, and unknown fields are rejected, so it cannot influence
authorization. The supervisor executes every search with the caller's ``AccessFilter``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from lean_rag.agents.base import AgentDeps, AgentRun, StrictModel
from lean_rag.agents.prompts import RETRIEVAL_SYSTEM, fence_evidence, schema_hint
from lean_rag.domain.models import SearchHit
from lean_rag.observability.logging import log_event
from lean_rag.textutil import content_terms, coverage

logger = logging.getLogger(__name__)


class PlanOut(StrictModel):
    route: Literal["search", "out_of_scope"]
    queries: list[str] = Field(default_factory=list, max_length=3)
    reason: str = Field(default="", max_length=300)


class AssessmentOut(StrictModel):
    sufficient: bool
    evidence: list[str] = Field(default_factory=list, max_length=10)
    follow_up_query: str | None = Field(default=None, max_length=300)
    reason: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class RetrievalPlan:
    route: Literal["search", "out_of_scope"]
    queries: list[str]
    decided_by: str


@dataclass(frozen=True)
class Assessment:
    sufficient: bool
    selected: list[SearchHit]
    follow_up_query: str | None
    decided_by: str


def _clean_queries(queries: list[str], question: str, limit: int) -> list[str]:
    cleaned = [" ".join(q.split())[:300] for q in queries if q and q.strip()]
    # The original question is always searched; rewrites can add recall but never replace it.
    return list(dict.fromkeys([question.strip(), *cleaned]))[:limit]


_SMALL_TALK = frozenset(
    "hi hello hey thanks thank cheers ok okay bye goodbye good morning afternoon evening".split()
)


def heuristic_plan(question: str, limit: int) -> RetrievalPlan:
    terms = content_terms(question)
    if not terms or set(terms) <= _SMALL_TALK:
        return RetrievalPlan("out_of_scope", [], "heuristic")
    return RetrievalPlan("search", _clean_queries([" ".join(terms)], question, limit), "heuristic")


def heuristic_assessment(question: str, hits: list[SearchHit], min_score: float, max_n: int) -> Assessment:
    selected = [h for h in hits if h.score >= min_score and coverage(question, h.chunk.text) >= 0.5][:max_n]
    return Assessment(bool(selected), selected, None, "heuristic")


class RetrievalAgent:
    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps
        self.limits = deps.settings.limits

    def plan(self, question: str, run: AgentRun) -> RetrievalPlan:
        fallback = heuristic_plan(question, self.limits.max_rewritten_queries)
        if not self.deps.uses_llm:
            return fallback
        assert self.deps.gateway is not None
        prompt = "\n\n".join(
            [
                f"User question:\n<untrusted_document>\n{question[:2000]}\n</untrusted_document>",
                "Decide the route. Use 'out_of_scope' only for greetings, requests to perform actions "
                "(delete, modify, call tools), or requests that cannot be answered from documents. "
                f"Otherwise 'search' and give up to {self.limits.max_rewritten_queries} short keyword "
                "queries that improve recall (synonyms, expanded acronyms). Do not add facts.",
                schema_hint({"route": "search", "queries": ["..."], "reason": "..."}),
            ]
        )
        out = self.deps.gateway.structured(
            agent="retrieval",
            system=RETRIEVAL_SYSTEM,
            prompt=prompt,
            schema=PlanOut,
            model_id=self.deps.settings.agent_model_id,
            budget=run.budget,
            max_tokens=300,
        )
        if out is None:
            log_event(logger, "retrieval_plan_fallback", logging.WARNING, reason="invalid_output")
            return fallback
        if out.route == "out_of_scope":
            return RetrievalPlan("out_of_scope", [], "llm")
        return RetrievalPlan(
            "search", _clean_queries(out.queries, question, self.limits.max_rewritten_queries), "llm"
        )

    def assess(self, question: str, hits: list[SearchHit], run: AgentRun, final_round: bool) -> Assessment:
        fallback = heuristic_assessment(
            question, hits, self.limits.min_evidence_score, self.limits.max_evidence_chunks
        )
        if not self.deps.uses_llm or not hits:
            return fallback
        assert self.deps.gateway is not None
        labelled = {f"E{i + 1}": h for i, h in enumerate(hits)}
        prompt = "\n\n".join(
            [
                f"User question:\n<untrusted_document>\n{question[:2000]}\n</untrusted_document>",
                "Candidate passages (already filtered to what this user may see):",
                *(fence_evidence(label, h.chunk.title, h.chunk.text[:1500]) for label, h in labelled.items()),
                f"Select up to {self.limits.max_evidence_chunks} passages that directly support an answer. "
                "Set sufficient=false if they do not answer the question."
                + ("" if final_round else " If insufficient, you may give one follow_up_query."),
                schema_hint(
                    {"sufficient": True, "evidence": ["E1"], "follow_up_query": None, "reason": "..."}
                ),
            ]
        )
        out = self.deps.gateway.structured(
            agent="retrieval",
            system=RETRIEVAL_SYSTEM,
            prompt=prompt,
            schema=AssessmentOut,
            model_id=self.deps.settings.agent_model_id,
            budget=run.budget,
            max_tokens=300,
        )
        if out is None:
            log_event(logger, "retrieval_assess_fallback", logging.WARNING, reason="invalid_output")
            return fallback
        # Unknown labels are ignored: the agent can only choose among chunks it was shown.
        selected = [labelled[e] for e in dict.fromkeys(out.evidence) if e in labelled]
        selected = selected[: self.limits.max_evidence_chunks]
        follow_up = None if final_round else (out.follow_up_query or None)
        return Assessment(out.sufficient and bool(selected), selected, follow_up, "llm")
