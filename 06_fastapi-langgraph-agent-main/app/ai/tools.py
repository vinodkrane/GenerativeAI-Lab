"""Tools available for the agent.
This file defines a set of tools that a Langgraph agent (LLM) can use. It demonstrates a clean pattern for creating tools.

Each tool has:
- A Pydantic input schema for validation.
- A tool function that performs the action.
- Error handling so the agent can recover gracefully.

`build_tools()` creates tools for each request and keeps
request-specific data (like user_id) hidden from the LLM.
"""

from __future__ import annotations

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Example tool 1: weather lookup
# ---------------------------------------------------------------------------
# This section defines a weather lookup tool that an AI agent can call.
class GetWeatherArgs(BaseModel):
    city: str = Field(..., description="City name, e.g. 'Sheffield' or 'Austin, TX'")


def _get_weather(city: str) -> str:
    try:
        # Placeholder for a real weather API call.
        return f"The weather in {city} is 18C and partly cloudy."
    except Exception as exc:  # pragma: no cover - defensive
        log.error("tool_error", tool="get_weather", error=str(exc))
        return f"Could not fetch weather for {city}: {exc}"


# ---------------------------------------------------------------------------
# Example tool 2: calculator
# ---------------------------------------------------------------------------
class CalculatorArgs(BaseModel):
    expression: str = Field(
        ..., description="A simple arithmetic expression, e.g. '12 * (4 + 1)'"
    )


_ALLOWED_CHARS = set("0123456789.+-*/() ")


def _calculate(expression: str) -> str:
    if not set(expression) <= _ALLOWED_CHARS:
        return "Error: expression contains unsupported characters."
    try:
        result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307 - char-whitelisted above
        return str(result)
    except Exception as exc:
        return f"Error evaluating expression: {exc}"


# ---------------------------------------------------------------------------
# Example tool 3: knowledge-base / RAG lookup
# ---------------------------------------------------------------------------
class SearchKnowledgeBaseArgs(BaseModel):
    query: str = Field(
        ..., description="What to search for in the internal knowledge base"
    )
    top_k: int = Field(
        default=3, ge=1, le=10, description="Number of results to return"
    )


def _search_knowledge_base(query: str, top_k: int = 3) -> str:
    try:
        # Placeholder for a real vector-store similarity search
        # (see app.services.prompt_builder for where retrieved
        # context is actually injected into the prompt).
        return f"No knowledge base configured yet. (query={query!r}, top_k={top_k})"
    except Exception as exc:  # pragma: no cover - defensive
        log.error("tool_error", tool="search_knowledge_base", error=str(exc))
        return f"Knowledge base search failed: {exc}"


def build_tools(*, user_id: str | None = None) -> list[StructuredTool]:
    """Create and return LangChain tools for a single request.

    Builds request-scoped tools (weather, calculator, knowledge search) and
    wraps them as StructuredTool objects. The user_id is captured through
    closure instead of being exposed in the tool schema, keeping sensitive
    context outside the LLM's control while allowing tools to use it for
    logging, authorization, quota checks, or auditing.

    Args:
        user_id: Optional identifier for the current user.

    Returns:
        A list of LangChain StructuredTool instances.
    """

    def _get_weather_scoped(city: str) -> str:
        log.info("tool_call", tool="get_weather", user_id=user_id, city=city)
        return _get_weather(city)

    def _calculate_scoped(expression: str) -> str:
        log.info("tool_call", tool="calculator", user_id=user_id)
        return _calculate(expression)

    def _search_kb_scoped(query: str, top_k: int = 3) -> str:
        log.info(
            "tool_call", tool="search_knowledge_base", user_id=user_id, query=query
        )
        return _search_knowledge_base(query, top_k=top_k)

    return [
        StructuredTool.from_function(
            func=_get_weather_scoped,
            name="get_weather",
            description="Get the current weather for a given city.",
            args_schema=GetWeatherArgs,
        ),
        StructuredTool.from_function(
            func=_calculate_scoped,
            name="calculator",
            description="Evaluate a simple arithmetic expression.",
            args_schema=CalculatorArgs,
        ),
        StructuredTool.from_function(
            func=_search_kb_scoped,
            name="search_knowledge_base",
            description="Search the internal knowledge base / document store for relevant context.",
            args_schema=SearchKnowledgeBaseArgs,
        ),
    ]
