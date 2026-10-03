"""Creates the ReAct agent that can use tools.

The agent follows this flow:
LLM decides → calls tool → receives result → generates final response.

A new agent is created for each request because the tools are
user-specific and contain the current user's context (user_id).
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph
from langchain.agents import create_agent

from app.ai.models import get_chat_model
from app.ai.tools import build_tools

_AGENT_SYSTEM_PROMPT_PLACEHOLDER = (
    "You are given the full system prompt as the first message in the "
    "conversation - see app.services.prompt_builder."
)


def build_react_agent(*, user_id: str) -> CompiledStateGraph:
    """Compile a fresh ReAct agent scoped to a single user's tools."""
    model = get_chat_model()
    tools = build_tools(user_id=user_id)
    return create_agent(model, tools=tools)
