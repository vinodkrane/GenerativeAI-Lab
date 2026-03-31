from .state import AgentState


async def formatter_node(state: AgentState) -> dict:
    """Format the draft answer as the final answer."""
    draft = state.get("draft_answer", "No answer generated.")
    score = state.get("critic_score", {})
    composite = score.get("composite", 0)

    confidence_note = ""
    if composite >= 0.85:
        confidence_note = "\n\n[Confidence: HIGH]"
    elif composite >= 0.6:
        confidence_note = "\n\n[Confidence: MEDIUM — verify key claims]"
    else:
        confidence_note = "\n\n[Confidence: LOW — answer may be incomplete]"

    return {"final_answer": draft + confidence_note}
