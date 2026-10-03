from __future__ import annotations
from typing import Annotated, TypedDict, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class RetrievedChunk(TypedDict):
    content: str
    source: str
    score: float
    chunk_type: str  # "vector" | "graph" | "episodic" | "tool"


class CriticScore(TypedDict):
    groundedness: float     # 0.0 – 1.0
    relevance: float        # 0.0 – 1.0
    completeness: float     # 0.0 – 1.0
    composite: float        # weighted average
    failure_reason: Optional[str]


class AgentState(TypedDict):
    # Core conversation
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    user_id: str

    # Planning
    task_queue: list[dict]      # [{sub_query, priority, tool_hints}]
    current_task: Optional[dict]
    plan_rationale: str

    # Retrieval
    retrieved_chunks: list[RetrievedChunk]
    fused_context: str
    retrieval_round: int
    rewritten_queries: list[str]

    # Reasoning & generation
    draft_answer: Optional[str]
    reasoning_trace: list[str]
    tool_results: list[dict]

    # Critic feedback
    critic_score: Optional[CriticScore]
    iteration_count: int
    token_budget_used: int

    # Memory
    episodic_context: list[str]
    final_answer: Optional[str]
