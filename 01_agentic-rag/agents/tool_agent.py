"""Tool agent: handles code execution, SQL, and web queries."""

from langchain_anthropic import ChatAnthropic
from .state import AgentState

_tool_llm = ChatAnthropic(model="claude-sonnet-4-5", max_tokens=1024)


async def tool_agent_node(state: AgentState) -> dict:
    """Decide what tool to call and execute it."""
    task = state["current_task"]
    hints = task.get("tool_hints", [])
    results = []

    if "code_exec" in hints:
        results.append({
            "tool": "code_exec",
            "result": "Code execution sandbox not configured."
        })

    if "web_search" in hints:
        results.append({
            "tool": "web_search",
            "result": "Web search not configured."
        })

    if "sql_query" in hints:
        results.append({
            "tool": "sql_query",
            "result": "SQL query execution not configured."
        })

    return {"tool_results": results}
