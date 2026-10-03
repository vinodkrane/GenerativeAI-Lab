from langgraph.graph import StateGraph, END
from agents.state import AgentState
from agents.planner import planner_node
from agents.memory import memory_load_node, memory_save_node
from agents.retriever import retriever_node
from agents.tool_agent import tool_agent_node
from agents.reasoner import reasoner_node
from agents.critic import critic_node, route_after_critic
from agents.formatter import formatter_node


def route_after_planner(state: AgentState) -> str:
    current_task = state.get("current_task")
    if not current_task:
        return "retriever"
    hints = current_task.get("tool_hints", [])
    if any(h in ["code_exec", "sql_query", "web_search"] for h in hints):
        return "tool_agent"
    return "retriever"


def build_graph(saver):
    builder = StateGraph(AgentState)

    builder.add_node("memory_load", memory_load_node)
    builder.add_node("planner", planner_node)
    builder.add_node("retriever", retriever_node)
    builder.add_node("tool_agent", tool_agent_node)
    builder.add_node("reasoner", reasoner_node)
    builder.add_node("critic", critic_node)
    builder.add_node("formatter", formatter_node)
    builder.add_node("memory_save", memory_save_node)

    builder.set_entry_point("memory_load")
    builder.add_edge("memory_load", "planner")

    builder.add_conditional_edges(
        "planner",
        route_after_planner,
        {"retriever": "retriever", "tool_agent": "tool_agent"},
    )

    builder.add_edge("tool_agent", "retriever")
    builder.add_edge("retriever", "reasoner")
    builder.add_edge("reasoner", "critic")

    builder.add_conditional_edges(
        "critic",
        route_after_critic,
        {"retriever": "retriever", "reasoner": "reasoner", "formatter": "formatter"},
    )

    builder.add_edge("formatter", "memory_save")
    builder.add_edge("memory_save", END)

    return builder.compile(checkpointer=saver)
