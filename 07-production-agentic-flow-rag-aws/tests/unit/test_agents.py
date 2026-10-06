"""Agent behaviour on the LLM path, driven by a scripted client."""

from __future__ import annotations

import json

import pytest

from helpers import POLICY_MD, ScriptedLLM
from lean_rag.agents.base import AgentDeps, AgentRun
from lean_rag.agents.chunking_agent import ChunkingAgent
from lean_rag.agents.generator import Generator
from lean_rag.agents.retrieval_agent import RetrievalAgent
from lean_rag.agents.verifier_agent import VerifierAgent, sample_chunks
from lean_rag.config import Settings
from lean_rag.domain.models import Chunk, SearchHit
from lean_rag.ingestion.chunking import Strategy
from lean_rag.ingestion.parsing import FileKind, parse_text
from lean_rag.llm.base import ModelGateway
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import Budget, BudgetExceeded


def _deps(settings: Settings, llm: ScriptedLLM) -> AgentDeps:
    metrics = Metrics("t", emit=False)
    return AgentDeps(settings=settings, metrics=metrics, gateway=ModelGateway(llm, settings, metrics))


def _run(calls: int = 10) -> AgentRun:
    return AgentRun(budget=Budget(calls, 100_000), agent_run_id="r")


def _hit(cid: str, text: str, score: float = 0.9) -> SearchHit:
    chunk = Chunk(
        chunk_id=cid,
        document_id="d",
        tenant_id="acme",
        document_version=1,
        ordinal=0,
        text=text,
        acl_principals=["group:staff"],
        content_hash="h",
        chunker_version="v",
    )
    return SearchHit(chunk=chunk, score=score)


def test_retrieval_plan_cannot_carry_acl_fields(settings: Settings) -> None:
    llm = ScriptedLLM(
        retrieval=json.dumps(
            {"route": "search", "queries": ["hotel"], "tenant_id": "globex", "acl_principals": ["*"]}
        )
    )
    deps = _deps(settings, llm)
    plan = RetrievalAgent(deps).plan("What is the hotel limit?", _run())
    # Unknown fields make the output invalid -> deterministic fallback, nothing from the model used.
    assert plan.decided_by == "heuristic"
    assert deps.metrics.counters["AgentInvalidOutput"] == 1


def test_retrieval_plan_always_keeps_original_question_and_bounds_rewrites(settings: Settings) -> None:
    llm = ScriptedLLM(retrieval=json.dumps({"route": "search", "queries": ["a b", "c d", "e f"]}))
    plan = RetrievalAgent(_deps(settings, llm)).plan("original question here", _run())
    assert plan.queries[0] == "original question here"
    assert len(plan.queries) == settings.limits.max_rewritten_queries


def test_retrieval_out_of_scope(settings: Settings) -> None:
    llm = ScriptedLLM(retrieval='{"route": "out_of_scope", "reason": "greeting"}')
    plan = RetrievalAgent(_deps(settings, llm)).plan("hello!", _run())
    assert plan.route == "out_of_scope" and plan.queries == []


def test_assessment_ignores_labels_it_was_not_shown(settings: Settings) -> None:
    llm = ScriptedLLM(retrieval='{"sufficient": true, "evidence": ["E2", "E99", "E2"]}')
    hits = [_hit("a", "alpha"), _hit("b", "hotel limit 180")]
    result = RetrievalAgent(_deps(settings, llm)).assess("hotel limit?", hits, _run(), final_round=True)
    assert [h.chunk.chunk_id for h in result.selected] == ["b"]
    assert result.sufficient and result.follow_up_query is None


def test_assessment_drops_follow_up_on_final_round(settings: Settings) -> None:
    llm = ScriptedLLM(retrieval='{"sufficient": false, "evidence": [], "follow_up_query": "more"}')
    agent = RetrievalAgent(_deps(settings, llm))
    assert agent.assess("q", [_hit("a", "x")], _run(), final_round=False).follow_up_query == "more"
    assert agent.assess("q", [_hit("a", "x")], _run(), final_round=True).follow_up_query is None


def test_chunking_agent_uses_valid_decision(settings: Settings) -> None:
    llm = ScriptedLLM(
        chunking=json.dumps(
            {
                "strategy": "paragraph",
                "target_chars": 800,
                "overlap_chars": 100,
                "title": "Travel Policy",
                "keywords": ["Travel", "Hotels"],
            }
        )
    )
    parsed = parse_text(POLICY_MD, FileKind.MARKDOWN)
    result = ChunkingAgent(_deps(settings, llm)).decide(parsed, _run())
    assert result.decided_by == "llm"
    assert result.plan.strategy is Strategy.PARAGRAPH and result.plan.target_chars == 800
    assert result.keywords == ["travel", "hotels"]
    assert "<untrusted_document>" in llm.calls[0][1]


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"strategy": "semantic", "target_chars": 800, "overlap_chars": 0}',
        '{"target_chars": 99999}',
    ],
)
def test_chunking_agent_falls_back_on_invalid_output(settings: Settings, response: str) -> None:
    parsed = parse_text(POLICY_MD, FileKind.MARKDOWN)
    result = ChunkingAgent(_deps(settings, ScriptedLLM(chunking=response))).decide(parsed, _run())
    assert result.decided_by == "heuristic"
    assert result.plan.strategy is Strategy.HEADING  # the doc has headings


def test_verifier_fills_in_probes_the_model_skipped(settings: Settings) -> None:
    hits = [_hit(f"c{i}", f"topic number {i} about hotels and meals") for i in range(3)]
    llm = ScriptedLLM(verifier='{"probes": [{"chunk_label": "C2", "query": "what about topic two"}]}')
    probes = VerifierAgent(_deps(settings, llm)).write_probes([h.chunk for h in hits], _run())
    assert [p.chunk_id for p in probes] == ["c0", "c1", "c2"]
    assert probes[1].query == "what about topic two"
    assert probes[0].query  # heuristic


def test_verifier_cannot_rechunk_past_the_bound(settings: Settings) -> None:
    llm = ScriptedLLM(verifier='{"action": "rechunk", "reason": "x"}')
    rec = VerifierAgent(_deps(settings, llm)).recommend([], 0.2, can_rechunk=False, run=_run())
    assert rec.action == "review"
    assert llm.calls == []  # not even asked


def test_sample_is_deterministic_and_spread() -> None:
    chunks = [_hit(str(i), "x").chunk for i in range(10)]
    assert [c.chunk_id for c in sample_chunks(chunks, 3)] == ["0", "3", "6"]


def test_generator_marks_unparseable_output(settings: Settings) -> None:
    llm = ScriptedLLM(generator="I think the answer is 180")
    draft = Generator(_deps(settings, llm)).generate("q", {"E1": _hit("a", "x").chunk}, _run())
    assert not draft.valid_output


def test_budget_stops_agent_calls(settings: Settings) -> None:
    llm = ScriptedLLM(retrieval='{"route": "search", "queries": []}')
    agent = RetrievalAgent(_deps(settings, llm))
    run = _run(calls=1)
    agent.plan("first question", run)
    with pytest.raises(BudgetExceeded):
        agent.plan("second question", run)
    assert llm.count("retrieval") == 1
