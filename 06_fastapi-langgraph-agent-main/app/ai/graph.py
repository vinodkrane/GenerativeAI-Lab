"""The end-to-end LangGraph pipeline:

input guardrail -> agent (LLM + tools) -> output guardrail

The complete chat flow runs as one traceable graph.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.ai.agent import build_react_agent
from app.ai.guardrails import check_input, check_output


# Graph state shared across all nodes during a single conversation turn
class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    session_id: str
    tool_calls: list[dict[str, Any]]


# Find the latest user message to validate before processing
def _guardrail_input_node(state: ChatState) -> dict:
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
    )
    if last_human is not None:
        check_input(str(last_human.content))
    return {}


# Build the agent with user-specific context and execute the LLM/tool loop
def _agent_node(state: ChatState) -> dict:
    agent = build_react_agent(user_id=state["user_id"])
    result = agent.invoke({"messages": state["messages"]})
    new_messages: list[AnyMessage] = result["messages"]

    # Pull out any tool invocations that happened during this turn so
    # the API layer can surface them in the response payload.
    tool_calls: list[dict[str, Any]] = []
    pending_calls: dict[str, dict[str, Any]] = {}
    for msg in new_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                pending_calls[call["id"]] = {
                    "name": call["name"],
                    "arguments": call["args"],
                }
        if isinstance(msg, ToolMessage):
            record = pending_calls.pop(
                msg.tool_call_id, {"name": msg.name, "arguments": {}}
            )
            record["result"] = msg.content
            tool_calls.append(record)

    return {"messages": new_messages, "tool_calls": tool_calls}


def _guardrail_output_node(state: ChatState) -> dict:
    # Validate the final AI response before sending it back to the user
    last_ai = next(
        (m for m in reversed(state["messages"]) if isinstance(m, AIMessage)),
        None,
    )

    if last_ai is None:
        return {}

    safe_content = check_output(str(last_ai.content))

    # Replace response content if output guardrail modified it
    if safe_content != last_ai.content:
        last_ai.content = safe_content

    return {}


def build_chat_graph() -> CompiledStateGraph:
    graph = StateGraph(ChatState)

    graph.add_node("guardrail_input", _guardrail_input_node)
    graph.add_node("agent", _agent_node)
    graph.add_node("guardrail_output", _guardrail_output_node)

    # Define the execution flow:
    # user input validation -> agent execution -> response validation
    graph.set_entry_point("guardrail_input")
    graph.add_edge("guardrail_input", "agent")
    graph.add_edge("agent", "guardrail_output")
    graph.set_finish_point("guardrail_output")

    return graph.compile()


# Compile once because the graph is stateless.
# User/session-specific information is stored inside ChatState.
chat_graph = build_chat_graph()
