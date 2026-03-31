import json
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from .state import AgentState

REASONER_SYSTEM = """You are a structured reasoning agent.

Given retrieved context and the current sub-task, reason through the answer
using this structure:

BRANCH A: Approach the question from angle 1. Support with evidence from context.
BRANCH B: Approach the question from angle 2. Support with evidence from context.

EVALUATION: Which branch has stronger evidence? Score each 0-10.

SYNTHESIS: Merge the best insights into a coherent, well-cited answer.

CONFIDENCE: State low/medium/high and any remaining gaps.

Always ground every claim in the retrieved context.
If context is insufficient, say so explicitly.
"""

_reasoner_llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    max_tokens=4096,
    temperature=0.2,
)


async def reasoner_node(state: AgentState) -> dict:
    print("  [reasoner] starting...")

    critic_feedback = "None"
    if state.get("critic_score") and state["critic_score"].get("failure_reason"):
        critic_feedback = state["critic_score"]["failure_reason"]

    tool_results_str = "None"
    if state.get("tool_results"):
        try:
            tool_results_str = json.dumps(state["tool_results"], indent=2)
        except Exception:
            tool_results_str = str(state["tool_results"])

    prompt = ChatPromptTemplate.from_messages([
        ("system", REASONER_SYSTEM),
        ("human", (
            "Sub-task: {sub_task}\n\n"
            "Original query: {user_query}\n\n"
            "Retrieved context:\n{fused_context}\n\n"
            "Tool results: {tool_results}\n\n"
            "Critic feedback from previous round: {critic_feedback}"
        )),
    ])

    chain = prompt | _reasoner_llm
    print("  [reasoner] calling LLM...")
    response = await chain.ainvoke({
        "sub_task": state["current_task"]["sub_query"],
        "user_query": state["user_query"],
        "fused_context": state["fused_context"],
        "tool_results": tool_results_str,
        "critic_feedback": critic_feedback,
    })
    print("  [reasoner] LLM responded.")

    tokens_used = 0
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        tokens_used = response.usage_metadata.get("total_tokens", 0)

    return {
        "draft_answer": response.content,
        "reasoning_trace": state["reasoning_trace"] + [response.content[:300]],
        "token_budget_used": state["token_budget_used"] + tokens_used,
    }