"""Model ports and the single gateway every agent uses to call an LLM.

The gateway enforces the per-run budget, retries transient failures, records tokens/cost,
and validates structured output against a Pydantic schema. Invalid output is returned as
``None`` - the calling agent decides the safe fallback.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from lean_rag.config import Settings
from lean_rag.observability.logging import log_event
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import Budget, with_retries

logger = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str


class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str, model_id: str, max_tokens: int) -> LLMResponse: ...


class Embedder(Protocol):
    model_id: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def rerank(self, query: str, texts: list[str], top_n: int) -> list[tuple[int, float]]:
        """Return (index into ``texts``, relevance score) best-first."""
        ...


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, object] | None:
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class ModelGateway:
    def __init__(self, client: LLMClient, settings: Settings, metrics: Metrics) -> None:
        self.client = client
        self.settings = settings
        self.metrics = metrics

    def estimate_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        price_in, price_out = self.settings.model_prices_per_1k.get(model_id, (0.0, 0.0))
        return (input_tokens / 1000) * price_in + (output_tokens / 1000) * price_out

    def structured(
        self,
        *,
        agent: str,
        system: str,
        prompt: str,
        schema: type[M],
        model_id: str,
        budget: Budget,
        max_tokens: int | None = None,
    ) -> M | None:
        budget.check()  # raises BudgetExceeded before spending anything
        limits = self.settings.limits
        started = time.perf_counter()
        response = with_retries(
            lambda: self.client.complete(
                system=system,
                prompt=prompt,
                model_id=model_id,
                max_tokens=max_tokens or limits.max_output_tokens,
            ),
            op=f"llm:{agent}",
            max_retries=limits.max_retries,
            base_delay=limits.retry_base_delay_s,
            max_delay=limits.retry_max_delay_s,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        cost = self.estimate_cost(model_id, response.input_tokens, response.output_tokens)
        budget.record(model_id, response.input_tokens, response.output_tokens, cost)
        self.metrics.put("AgentCalls", 1, Agent=agent)
        self.metrics.put("AgentLatency", latency_ms, "Milliseconds", Agent=agent)
        self.metrics.put("Tokens", response.input_tokens + response.output_tokens, Agent=agent)

        data = extract_json(response.text)
        parsed: M | None = None
        if data is not None:
            try:
                parsed = schema.model_validate(data)
            except ValidationError:
                parsed = None
        log_event(
            logger,
            "agent_call",
            agent=agent,
            model_id=model_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=round(latency_ms, 1),
            cost_usd=round(cost, 6),
            valid_output=parsed is not None,
        )
        if parsed is None:
            self.metrics.put("AgentInvalidOutput", 1, Agent=agent)
        return parsed
