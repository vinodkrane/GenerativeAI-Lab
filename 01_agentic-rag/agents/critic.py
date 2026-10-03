import json
from langchain_anthropic import ChatAnthropic
from .state import AgentState, CriticScore

# Strict evaluation LLM
_judge_llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    max_tokens=512,
    temperature=0.0,
)

PASS_THRESHOLD = 0.85
MAX_ITERATIONS = 3
TOKEN_BUDGET = 80_000


async def critic_node(state: AgentState) -> dict:
    print("  [critic] starting...")

    system_msg = (
        "You are a strict evaluation agent. "
        "Score the draft answer against the retrieved context. "
        "Return ONLY a valid JSON object — no explanation, no markdown, no code fences. "
        "Use exactly this schema:\n"
        '{"groundedness": 0.0, "relevance": 0.0, "completeness": 0.0, '
        '"composite": 0.0, "failure_reason": null}\n\n'
        "Where:\n"
        "- groundedness: 0.0-1.0  Is every claim supported by the context?\n"
        "- relevance:    0.0-1.0  Does the answer address the actual question?\n"
        "- completeness: 0.0-1.0  Are all aspects of the question covered?\n"
        "- composite:    weighted average = 0.7*groundedness + 0.2*relevance + 0.1*completeness\n"
        "- failure_reason: string describing what is wrong, or null if composite >= 0.85"
    )

    human_msg = (
        f"Question: {state['user_query']}\n\n"
        f"Context (truncated):\n{state['fused_context'][:4000]}\n\n"
        f"Draft answer:\n{state['draft_answer']}"
    )

    print("  [critic] calling LLM...")
    response = await _judge_llm.ainvoke([
        ("system", system_msg),
        ("human", human_msg),
    ])
    print(f"  [critic] raw response: {response.content[:200]}")

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        score: CriticScore = json.loads(raw)
        # Ensure defaults
        score.setdefault("groundedness", 0.5)
        score.setdefault("relevance", 0.5)
        score.setdefault("completeness", 0.5)
        score.setdefault("composite", 0.5)
        score.setdefault("failure_reason", "Parse fallback")
    except json.JSONDecodeError as e:
        print(f"  [critic] JSON parse failed: {e} — using default score")
        score = {
            "groundedness": 0.5,
            "relevance": 0.5,
            "completeness": 0.5,
            "composite": 0.5,
            "failure_reason": "Judge response was malformed",
        }

    print(f"  [critic] score={score['composite']:.2f}")
    return {
        "critic_score": score,
        "iteration_count": state["iteration_count"] + 1,
    }


def route_after_critic(state: AgentState) -> str:
    score = state["critic_score"]
    iters = state["iteration_count"]
    budget = state["token_budget_used"]

    if iters >= MAX_ITERATIONS or budget >= TOKEN_BUDGET:
        print(f"  [critic] Hard limit hit (iters={iters}, budget={budget}) — exiting loop")
        return "formatter"

    if score["composite"] >= PASS_THRESHOLD:
        return "formatter"

    if score.get("groundedness", 1.0) < 0.6:
        return "retriever"

    return "reasoner"
