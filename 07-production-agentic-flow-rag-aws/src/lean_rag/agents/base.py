"""Shared agent plumbing.

Every agent has two policies with the same output type:

* an LLM policy (Bedrock via ``ModelGateway``) used when models=bedrock, and
* a deterministic heuristic policy, used for local/offline runs, as the fallback when the
  model returns invalid output, and as the baseline the evaluation compares against.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from lean_rag.config import Settings
from lean_rag.llm.base import ModelGateway
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import Budget


class StrictModel(BaseModel):
    """Agent outputs reject unknown fields - e.g. a model inventing a ``tenant_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass
class AgentRun:
    """Per-request context: shared budget and agent run id for correlation."""

    budget: Budget
    agent_run_id: str


@dataclass
class AgentDeps:
    settings: Settings
    metrics: Metrics
    gateway: ModelGateway | None  # None -> heuristic policies only

    @property
    def uses_llm(self) -> bool:
        return self.gateway is not None
