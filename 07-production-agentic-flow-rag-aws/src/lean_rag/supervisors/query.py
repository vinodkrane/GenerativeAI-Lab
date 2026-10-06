"""Query supervisor: authenticate -> ACL -> plan -> search -> rerank -> assess -> generate ->
validate -> answer or abstain. Every loop is bounded by ``Limits``."""

from __future__ import annotations

import logging
import time
import uuid

from lean_rag.agents.base import AgentRun
from lean_rag.agents.citations import Sentence, validate_answer
from lean_rag.agents.generator import Generator
from lean_rag.agents.retrieval_agent import Assessment, RetrievalAgent
from lean_rag.config import Settings
from lean_rag.domain.models import Chunk, Citation, QueryResult, SearchHit, User
from lean_rag.observability.logging import log_context, log_event
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import Budget, BudgetExceeded
from lean_rag.retrieval.hybrid import HybridRetriever
from lean_rag.security.acl import AccessFilter

logger = logging.getLogger(__name__)

ABSTAIN_MESSAGE = (
    "I couldn't find enough information in the documents available to you to answer that reliably."
)
OUT_OF_SCOPE_MESSAGE = "I can only answer questions using the documents available to you."


def render_answer(sentences: list[Sentence]) -> str:
    return " ".join(f"{s.text} [{', '.join(s.citations)}]" for s in sentences)


class QuerySupervisor:
    def __init__(
        self,
        *,
        settings: Settings,
        retriever: HybridRetriever,
        retrieval_agent: RetrievalAgent,
        generator: Generator,
        metrics: Metrics,
    ) -> None:
        self.settings = settings
        self.limits = settings.limits
        self.retriever = retriever
        self.retrieval_agent = retrieval_agent
        self.generator = generator
        self.metrics = metrics

    def answer(self, user: User, question: str) -> QueryResult:
        query_id = uuid.uuid4().hex
        started = time.perf_counter()
        # Authorization is fixed here, before any model sees the question.
        acl = AccessFilter.for_user(user)
        run = AgentRun(
            budget=Budget(self.limits.max_llm_calls_per_query, self.limits.max_tokens_per_query),
            agent_run_id=uuid.uuid4().hex,
        )
        with log_context(query_id=query_id, tenant_id=user.tenant_id, agent_run_id=run.agent_run_id):
            retrieved: list[str] = []
            try:
                result = self._run(query_id, question, acl, run, retrieved)
            except BudgetExceeded as exc:
                log_event(logger, "query_budget_exceeded", logging.WARNING, reason=str(exc))
                result = self._abstain(query_id, "budget_exceeded", retrieved)
            result.llm_calls = run.budget.calls
            result.input_tokens = run.budget.input_tokens
            result.output_tokens = run.budget.output_tokens
            result.estimated_cost_usd = round(run.budget.cost_usd, 6)
            result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            self.metrics.put("Queries", 1)
            self.metrics.put("QueryLatency", result.latency_ms, "Milliseconds")
            self.metrics.put("QueryCostMicroUSD", result.estimated_cost_usd * 1_000_000, "None")
            if result.abstained:
                self.metrics.put("Abstentions", 1, Reason=result.reason or "unknown")
            log_event(
                logger,
                "query_completed",
                question_chars=len(question),
                abstained=result.abstained,
                reason=result.reason,
                citations=len(result.citations),
                llm_calls=result.llm_calls,
                tokens=result.input_tokens + result.output_tokens,
                latency_ms=result.latency_ms,
            )
            return result

    def _run(
        self, query_id: str, question: str, acl: AccessFilter, run: AgentRun, retrieved: list[str]
    ) -> QueryResult:
        plan = self.retrieval_agent.plan(question, run)
        if plan.route == "out_of_scope":
            return self._abstain(query_id, "out_of_scope", retrieved, OUT_OF_SCOPE_MESSAGE)

        assessment = self._retrieve(question, plan.queries, acl, run, retrieved)
        if assessment is None:
            return self._abstain(query_id, "insufficient_evidence", retrieved)

        evidence: dict[str, Chunk] = {f"E{i + 1}": h.chunk for i, h in enumerate(assessment.selected)}
        feedback: str | None = None
        for attempt in range(1, self.limits.generation_attempts + 1):
            draft = self.generator.generate(question, evidence, run, feedback)
            if draft.insufficient:
                return self._abstain(query_id, "insufficient_evidence", retrieved)
            if not draft.valid_output:
                feedback = "the response was not valid JSON matching the schema"
                continue
            validation = validate_answer(draft.sentences, evidence)
            self.metrics.put("Groundedness", validation.groundedness * 100, "Percent")
            if validation.ok:
                return self._respond(query_id, draft.sentences, evidence, retrieved)
            self.metrics.put("CitationValidationFailures", 1, Attempt=str(attempt))
            feedback = "; ".join(validation.problems[:5])
            log_event(
                logger, "citation_validation_failed", logging.WARNING, attempt=attempt, problems=feedback
            )
        return self._abstain(query_id, "citation_validation_failed", retrieved)

    def _retrieve(
        self, question: str, queries: list[str], acl: AccessFilter, run: AgentRun, retrieved: list[str]
    ) -> Assessment | None:
        for round_no in range(1, self.limits.max_search_rounds + 1):
            final = round_no == self.limits.max_search_rounds
            hits: list[SearchHit] = self.retriever.search(queries, acl)
            reranked = self.retriever.rerank(question, hits)
            retrieved[:] = list(dict.fromkeys([*retrieved, *(h.chunk.chunk_id for h in reranked)]))
            assessment = self.retrieval_agent.assess(question, reranked, run, final_round=final)
            if assessment.sufficient:
                return assessment
            if final or not assessment.follow_up_query:
                return None
            queries = [question, assessment.follow_up_query]
        return None

    def _respond(
        self, query_id: str, sentences: list[Sentence], evidence: dict[str, Chunk], retrieved: list[str]
    ) -> QueryResult:
        used = list(dict.fromkeys(c for s in sentences for c in s.citations))
        citations = [
            Citation(
                evidence_id=label,
                chunk_id=evidence[label].chunk_id,
                document_id=evidence[label].document_id,
                title=evidence[label].title,
                snippet=evidence[label].text[:280],
            )
            for label in used
        ]
        return QueryResult(
            query_id=query_id,
            answer=render_answer(sentences),
            abstained=False,
            citations=citations,
            retrieved_chunk_ids=list(retrieved),
        )

    def _abstain(
        self, query_id: str, reason: str, retrieved: list[str], message: str = ABSTAIN_MESSAGE
    ) -> QueryResult:
        return QueryResult(
            query_id=query_id,
            answer=message,
            abstained=True,
            reason=reason,
            retrieved_chunk_ids=list(retrieved),
        )
