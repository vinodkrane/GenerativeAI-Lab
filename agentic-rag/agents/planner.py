import json
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from .state import AgentState

PLANNER_SYSTEM = """
You are a strategic planning agent in an Agentic RAG system.

Given a user query and any retrieved episodic memory from past sessions,
decompose the query into an ordered list of sub-tasks. Each task must specify:
- sub_query: the specific information need for this step
- priority: integer 1-5 (1 = highest)
- tool_hints: list of tools that may be needed (vector_search, graph_query,
              code_exec, sql_query, web_search)
- complexity: "simple" | "complex" (routes to appropriate model tier)

Output ONLY valid JSON. Format:
{{
  "task_queue": [...],
  "rationale": "one-sentence planning rationale"
}}
"""

planner_llm = ChatAnthropic(
    model="claude-sonnet-4-5",
    max_tokens=2048,
    temperature=0.0,
)

planner_prompt = ChatPromptTemplate.from_messages([
    ("system", PLANNER_SYSTEM),
    ("human", "User query: {user_query}\n\nPast episode context:\n{episodic_context}"),
])


async def planner_node(state: AgentState) -> dict:
    episodic_str = "\n".join(state["episodic_context"]) or "No prior context."
    chain = planner_prompt | planner_llm
    response = await chain.ainvoke({
        "user_query": state["user_query"],
        "episodic_context": episodic_str,
    })

    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
        tasks = parsed.get("task_queue", [])
        if not tasks:
            raise ValueError("Empty task_queue")
        return {
            "task_queue": tasks,
            "plan_rationale": parsed.get("rationale", ""),
            "current_task": tasks[0],
            "iteration_count": 0,
            "retrieval_round": 0,
        }
    except Exception as e:
        print(f"  [planner] JSON parse failed ({e}), using fallback single-task plan")
        fallback_task = {
            "sub_query": state["user_query"],
            "priority": 1,
            "tool_hints": ["vector_search"],
            "complexity": "complex",
        }
        return {
            "task_queue": [fallback_task],
            "plan_rationale": "Fallback: single-task plan",
            "current_task": fallback_task,
            "iteration_count": 0,
            "retrieval_round": 0,
        }
