"""Naive RAG baseline used only by the evaluation, to measure what the agents add.

Single vector query, no query planning, no reranking, no evidence assessment, no citation
validation and no regeneration. It shares the index, embedder and generator with the
agentic pipeline so the comparison isolates the agentic steps.
"""

from __future__ import annotations

import time
import uuid

from lean_rag.agents.base import AgentRun
from lean_rag.agents.generator import Generator
from lean_rag.config import Limits
from lean_rag.domain.models import Chunk, Citation, QueryResult, User
from lean_rag.reliability import Budget, BudgetExceeded
from lean_rag.retrieval.hybrid import HybridRetriever
from lean_rag.security.acl import AccessFilter
from lean_rag.supervisors.query import ABSTAIN_MESSAGE, render_answer


class NaiveRag:
    def __init__(self, retriever: HybridRetriever, generator: Generator, limits: Limits) -> None:
        self.retriever = retriever
        self.generator = generator
        self.limits = limits

    def answer(self, user: User, question: str) -> QueryResult:
        started = time.perf_counter()
        acl = AccessFilter.for_user(user)
        run = AgentRun(
            Budget(self.limits.max_llm_calls_per_query, self.limits.max_tokens_per_query), "baseline"
        )
        hits = self.retriever.search([question], acl, use_lexical=False)[: self.limits.max_evidence_chunks]
        evidence: dict[str, Chunk] = {f"E{i + 1}": h.chunk for i, h in enumerate(hits)}
        result = QueryResult(
            query_id=uuid.uuid4().hex,
            answer=ABSTAIN_MESSAGE,
            abstained=True,
            reason="insufficient_evidence",
            retrieved_chunk_ids=[h.chunk.chunk_id for h in hits],
        )
        try:
            draft = self.generator.generate(question, evidence, run) if evidence else None
        except BudgetExceeded:
            draft = None
        if draft and draft.valid_output and not draft.insufficient and draft.sentences:
            used = list(dict.fromkeys(c for s in draft.sentences for c in s.citations if c in evidence))
            result.answer = render_answer(draft.sentences)
            result.abstained = False
            result.reason = None
            result.citations = [
                Citation(
                    evidence_id=label,
                    chunk_id=evidence[label].chunk_id,
                    document_id=evidence[label].document_id,
                    title=evidence[label].title,
                    snippet=evidence[label].text[:280],
                )
                for label in used
            ]
        result.llm_calls = run.budget.calls
        result.input_tokens = run.budget.input_tokens
        result.output_tokens = run.budget.output_tokens
        result.estimated_cost_usd = round(run.budget.cost_usd, 6)
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return result
