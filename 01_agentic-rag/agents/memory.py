import json
from pathlib import Path
from .state import AgentState

MEMORY_FILE = Path("episodic_memory.json")


def _load_memory_store() -> list:
    if MEMORY_FILE.exists():
        with open(MEMORY_FILE) as f:
            return json.load(f)
    return []


def _save_memory_store(episodes: list):
    with open(MEMORY_FILE, "w") as f:
        json.dump(episodes[-50:], f, indent=2)


async def memory_load_node(state: AgentState) -> dict:
    episodes = _load_memory_store()
    user_episodes = [
        e["summary"] for e in episodes
        if e.get("user_id") == state["user_id"]
    ][-3:]
    return {
        "episodic_context": user_episodes,
        "tool_results": [],
        "reasoning_trace": [],
        "rewritten_queries": [],
        "token_budget_used": 0,
    }


async def memory_save_node(state: AgentState) -> dict:
    score = state.get("critic_score") or {}
    if score.get("composite", 0) >= 0.7:
        episodes = _load_memory_store()
        episodes.append({
            "user_id": state["user_id"],
            "query": state["user_query"],
            "summary": f"Query: {state['user_query'][:100]}. Iterations: {state['iteration_count']}. Score: {score.get('composite', 0):.2f}",
            "quality": score.get("composite", 0),
            "iterations": state["iteration_count"],
        })
        _save_memory_store(episodes)
    return {}