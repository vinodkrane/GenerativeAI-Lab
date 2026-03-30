"""
========================================================
LangGraph 1.1.x — All Five Memory Tiers
========================================================
Each section is self-contained and runnable independently.

Install requirements:
    pip install langgraph>=1.1.0 langchain-openai langchain-core

Set your key:
    export OPENAI_API_KEY="sk-..."
"""

# ─────────────────────────────────────────────────────────────────────────────
# TIER 1 — IN-CONTEXT MEMORY
# What it is: The live conversation window. Messages stay in the state object
#             and are passed directly to the LLM on every turn.
# LangGraph tool: add_messages reducer on the State TypedDict
# ─────────────────────────────────────────────────────────────────────────────

from typing import Annotated
from typing_extensions import TypedDict
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages


class InContextState(TypedDict):
    # add_messages is the reducer: it appends new messages instead of
    # overwriting, so the full conversation history is preserved in-state.
    messages: Annotated[list, add_messages]


def in_context_chat_node(state: InContextState) -> dict:
    """Calls the LLM with the full message history from state."""
    llm = ChatOpenAI(model="gpt-4o-mini")
    system = SystemMessage(content="You are a helpful assistant.")
    # The entire conversation lives in state["messages"] — no extra retrieval.
    response = llm.invoke([system] + state["messages"])
    return {"messages": [response]}


def build_in_context_graph():
    builder = StateGraph(InContextState)
    builder.add_node("chat", in_context_chat_node)
    builder.add_edge(START, "chat")
    builder.add_edge("chat", END)
    return builder.compile()


def demo_in_context():
    print("\n" + "=" * 60)
    print("TIER 1 — IN-CONTEXT MEMORY")
    print("=" * 60)

    graph = build_in_context_graph()
    config = {}  # No checkpointer needed; state lives only for this call.

    # Turn 1
    state = graph.invoke(
        {"messages": [HumanMessage(content="My name is Alice.")]},
        config,
    )
    print("User: My name is Alice.")
    print("AI  :", state["messages"][-1].content)

    # Turn 2 — pass the updated state so previous messages stay in context
    state = graph.invoke(
        {"messages": state["messages"] + [HumanMessage(content="What is my name?")]},
        config,
    )
    print("User: What is my name?")
    print("AI  :", state["messages"][-1].content)


# ─────────────────────────────────────────────────────────────────────────────
# TIER 2 — WORKING MEMORY
# What it is: A structured scratchpad that tracks the current task's progress:
#             goals, steps tried, intermediate results, and next action.
# LangGraph tool: MemorySaver checkpointer (persists state across graph steps)
# ─────────────────────────────────────────────────────────────────────────────

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END


class WorkingMemoryState(TypedDict):
    messages: Annotated[list, add_messages]
    # Scratchpad holds what the agent has tried and its current plan.
    scratchpad: list[str]
    current_goal: str


def working_memory_node(state: WorkingMemoryState) -> dict:
    """
    Reads the scratchpad to avoid repeating steps,
    then updates it with what it just did.
    """
    llm = ChatOpenAI(model="gpt-4o-mini")

    scratchpad_text = "\n".join(state["scratchpad"]) if state["scratchpad"] else "No steps taken yet."
    goal = state.get("current_goal", "No goal set.")

    system_prompt = f"""You are a task-solving agent.

Current goal: {goal}

Progress so far (working memory / scratchpad):
{scratchpad_text}

Based on the above, decide the single next best action and respond briefly.
If the task is complete, start your reply with 'DONE:'."""

    response = llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
    reply = response.content

    # Update the scratchpad — this is the "write-back" step of working memory.
    new_step = f"Step {len(state['scratchpad']) + 1}: {reply[:120]}"
    updated_scratchpad = state["scratchpad"] + [new_step]

    return {
        "messages": [response],
        "scratchpad": updated_scratchpad,
    }


def build_working_memory_graph():
    checkpointer = MemorySaver()   # Persists state between .invoke() calls
    builder = StateGraph(WorkingMemoryState)
    builder.add_node("agent", working_memory_node)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)


def demo_working_memory():
    print("\n" + "=" * 60)
    print("TIER 2 — WORKING MEMORY (checkpointed scratchpad)")
    print("=" * 60)

    graph = build_working_memory_graph()
    # thread_id groups a series of steps into one task session.
    config = {"configurable": {"thread_id": "task-001"}}

    # Step 1
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="Start working on this task: write a haiku about autumn.")],
            "scratchpad": [],
            "current_goal": "Write a haiku about autumn",
        },
        config,
    )
    print("Step 1 →", state["messages"][-1].content)
    print("Scratchpad:", state["scratchpad"])

    # Step 2 — same thread_id; LangGraph restores state from the checkpointer.
    state = graph.invoke(
        {"messages": [HumanMessage(content="Refine the haiku — make it more melancholy.")]},
        config,
    )
    print("\nStep 2 →", state["messages"][-1].content)
    print("Scratchpad:", state["scratchpad"])


# ─────────────────────────────────────────────────────────────────────────────
# TIER 3 — EPISODIC MEMORY
# What it is: A record of past conversations/events the agent can recall later.
#             Stored as documents in an external store; retrieved by similarity.
# LangGraph tool: InMemoryStore (swap for PostgresStore in production)
# ─────────────────────────────────────────────────────────────────────────────

import uuid
import datetime
from langgraph.store.memory import InMemoryStore


# The store lives outside the graph so it persists across sessions.
episodic_store = InMemoryStore()


class EpisodicState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: str
    retrieved_episodes: list[str]  # Past episodes injected into this turn


def retrieve_episodes_node(state: EpisodicState) -> dict:
    user_message = state["messages"][-1].content
    namespace = (state["user_id"], "episodes")

    # Use global store instead of keyword argument
    results = episodic_store.search(namespace, query=user_message, limit=3)

    episodes = [
        f"[{item.value.get('timestamp', 'unknown')}] {item.value.get('summary', '')}"
        for item in results
    ]
    return {"retrieved_episodes": episodes}


def episodic_chat_node(state: EpisodicState) -> dict:
    """Responds using current messages + injected past episodes."""
    llm = ChatOpenAI(model="gpt-4o-mini")

    episodes_text = (
        "\n".join(state["retrieved_episodes"])
        if state["retrieved_episodes"]
        else "No relevant past episodes found."
    )

    system_prompt = f"""You are a helpful assistant with memory of past conversations.

Relevant past episodes:
{episodes_text}

Use these past episodes to personalise your response where relevant."""

    response = llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
    return {"messages": [response]}


def save_episode_node(state: EpisodicState) -> dict:
    namespace = (state["user_id"], "episodes")

    last_user_msg = next(
        (m.content for m in reversed(state["messages"]) if m.type == "human"), ""
    )
    last_ai_msg = next(
        (m.content for m in reversed(state["messages"]) if m.type == "ai"), ""
    )

    episode = {
        "summary": f"User asked: '{last_user_msg[:100]}'. Agent replied: '{last_ai_msg[:200]}'",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }

    # Use global store
    episodic_store.put(namespace, str(uuid.uuid4()), episode)
    return {}  # No state change needed


def build_episodic_graph():
    checkpointer = MemorySaver()
    builder = StateGraph(EpisodicState)
    builder.add_node("retrieve_episodes", retrieve_episodes_node)
    builder.add_node("chat", episodic_chat_node)
    builder.add_node("save_episode", save_episode_node)
    builder.add_edge(START, "retrieve_episodes")
    builder.add_edge("retrieve_episodes", "chat")
    builder.add_edge("chat", "save_episode")
    builder.add_edge("save_episode", END)
    # Pass the store into graph nodes via the store= parameter on compile().
    return builder.compile(checkpointer=checkpointer, store=episodic_store)


def demo_episodic():
    print("\n" + "=" * 60)
    print("TIER 3 — EPISODIC MEMORY (past-conversation recall)")
    print("=" * 60)

    graph = build_episodic_graph()
    user_id = "user_alice"

    # Session 1 — plant a memory
    config_s1 = {"configurable": {"thread_id": "session-001"}}
    graph.invoke(
        {
            "messages": [HumanMessage(content="I love hiking in the Lake District.")],
            "user_id": user_id,
            "retrieved_episodes": [],
        },
        config_s1,
        store=episodic_store  # <-- pass the store here
    )
    print("Session 1: User mentioned hiking in the Lake District (saved to episodic store).")

    # Session 2 — new thread; episodic store bridges the gap
    config_s2 = {"configurable": {"thread_id": "session-002"}}
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="Any outdoor activity ideas for the weekend?")],
            "user_id": user_id,
            "retrieved_episodes": [],
        },
        config_s2,
        store=episodic_store  # <-- pass the store here as well
    )
    print("Session 2 — User: Any outdoor activity ideas for the weekend?")
    print("AI (with episodic recall):", state["messages"][-1].content)

# ─────────────────────────────────────────────────────────────────────────────
# TIER 4 — SEMANTIC MEMORY
# What it is: Structured, named facts about a user or domain that persist
#             across sessions (preferences, profile, domain rules).
# LangGraph tool: InMemoryStore with namespaced key-value documents
# ─────────────────────────────────────────────────────────────────────────────

semantic_store = InMemoryStore()


class SemanticState(TypedDict):
    messages: Annotated[list, add_messages]
    user_id: str
    user_facts: dict  # Facts loaded from the semantic store for this turn


def load_user_facts_node(state: SemanticState) -> dict:
    """
    Loads structured user facts from the semantic store before the agent responds.
    """
    namespace = (state["user_id"], "semantic")
    item = semantic_store.get(namespace, "profile")
    facts = item.value if item else {}
    return {"user_facts": facts}


def semantic_chat_node(state: SemanticState) -> dict:
    """Responds using the injected user facts."""
    llm = ChatOpenAI(model="gpt-4o-mini")

    facts_text = (
        "\n".join(f"  - {k}: {v}" for k, v in state["user_facts"].items())
        if state["user_facts"]
        else "  (no facts stored yet)"
    )

    system_prompt = f"""You are a personalised assistant.

Known facts about this user:
{facts_text}

Use these facts naturally to tailor your responses."""

    response = llm.invoke([SystemMessage(content=system_prompt)] + state["messages"])
    return {"messages": [response]}


def update_user_facts_node(state: SemanticState) -> dict:
    """
    Demonstrates updating a fact. In a real app, an LLM would decide what to
    extract and upsert; here we hard-code a preference for illustration.
    """
    namespace = (state["user_id"], "semantic")
    existing = semantic_store.get(namespace, "profile")
    facts = existing.value.copy() if existing else {}

    last_msg = next(
        (m.content for m in reversed(state["messages"]) if m.type == "human"), ""
    )
    if "bullet" in last_msg.lower():
        facts["response_style"] = "bullet_points"
    if "london" in last_msg.lower():
        facts["city"] = "London"

    if facts:
        semantic_store.put(namespace, "profile", facts)
    return {}


def build_semantic_graph():
    checkpointer = MemorySaver()
    builder = StateGraph(SemanticState)
    builder.add_node("load_facts", load_user_facts_node)
    builder.add_node("chat", semantic_chat_node)
    builder.add_node("update_facts", update_user_facts_node)
    builder.add_edge(START, "load_facts")
    builder.add_edge("load_facts", "chat")
    builder.add_edge("chat", "update_facts")
    builder.add_edge("update_facts", END)
    return builder.compile(checkpointer=checkpointer, store=semantic_store)


def demo_semantic():
    print("\n" + "=" * 60)
    print("TIER 4 — SEMANTIC MEMORY (structured user facts)")
    print("=" * 60)

    graph = build_semantic_graph()
    user_id = "user_bob"
    namespace = (user_id, "semantic")

    # Pre-seed some facts directly (in production these accumulate over time).
    semantic_store.put(
        namespace,
        "profile",
        {
            "name": "Bob",
            "timezone": "Europe/London",
            "report_format": "spreadsheet",
            "response_style": "concise",
        },
    )
    print("Pre-seeded facts: name=Bob, timezone=Europe/London, report_format=spreadsheet")

    config = {"configurable": {"thread_id": "sem-001"}}
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="What format should I use for the Q3 report?")],
            "user_id": user_id,
            "user_facts": {},
        },
        config,
    )
    print("User: What format should I use for the Q3 report?")
    print("AI (with semantic facts):", state["messages"][-1].content)


# ─────────────────────────────────────────────────────────────────────────────
# TIER 5 — PROCEDURAL MEMORY
# What it is: The agent's standing behaviours — its "muscle memory".
#             Encoded in the system prompt (soft procedural memory) or
#             model fine-tuning (hard procedural memory).
# LangGraph tool: A persistent system prompt injected at graph compile time,
#                 optionally stored and updated in InMemoryStore.
# ─────────────────────────────────────────────────────────────────────────────

procedural_store = InMemoryStore()

# Default procedures — what the agent always does, regardless of user input.
DEFAULT_PROCEDURES = """You are a professional software assistant. Always follow these rules:
1. Always start code examples with a one-line comment explaining what they do.
2. Summarise your answer in one sentence before going into detail.
3. If asked about security, flag risks before giving the solution.
4. Keep replies under 200 words unless the user asks for more detail.
5. Use British English spelling."""


class ProceduralState(TypedDict):
    messages: Annotated[list, add_messages]
    agent_id: str


def load_procedures_node(state: ProceduralState) -> dict:
    """
    Loads the current procedural instructions from the store.
    Falls back to DEFAULT_PROCEDURES on first run.
    """
    namespace = (state["agent_id"], "procedural")
    item = procedural_store.get(namespace, "instructions")
    if item is None:
        # First run: seed the store with defaults
        procedural_store.put(namespace, "instructions", {"text": DEFAULT_PROCEDURES})
    return {}


def procedural_chat_node(state: ProceduralState) -> dict:
    """
    Reads live procedural instructions and injects them as the system prompt.
    """
    llm = ChatOpenAI(model="gpt-4o-mini")
    namespace = (state["agent_id"], "procedural")
    item = procedural_store.get(namespace, "instructions")
    procedures = item.value["text"] if item else DEFAULT_PROCEDURES

    response = llm.invoke([SystemMessage(content=procedures)] + state["messages"])
    return {"messages": [response]}



def update_procedures_node(state: ProceduralState) -> dict:
    """
    Example runtime update (e.g. verbose mode).
    """
    last_msg = next(
        (m.content for m in reversed(state["messages"]) if m.type == "human"), ""
    )
    if "verbose" in last_msg.lower():
        namespace = (state["agent_id"], "procedural")
        item = procedural_store.get(namespace, "instructions")
        current = item.value["text"] if item else DEFAULT_PROCEDURES
        updated = current.replace(
            "Keep replies under 200 words",
            "Replies may be as long as needed"
        )
        procedural_store.put(namespace, "instructions", {"text": updated})
        print("  [Procedural memory updated: verbose mode enabled]")
    return {}


def build_procedural_graph():
    checkpointer = MemorySaver()
    builder = StateGraph(ProceduralState)
    builder.add_node("load_procedures", load_procedures_node)
    builder.add_node("chat", procedural_chat_node)
    builder.add_node("update_procedures", update_procedures_node)
    builder.add_edge(START, "load_procedures")
    builder.add_edge("load_procedures", "chat")
    builder.add_edge("chat", "update_procedures")
    builder.add_edge("update_procedures", END)
    return builder.compile(checkpointer=checkpointer, store=procedural_store)


def demo_procedural():
    print("\n" + "=" * 60)
    print("TIER 5 — PROCEDURAL MEMORY (standing behaviours / system prompt)")
    print("=" * 60)

    graph = build_procedural_graph()
    agent_id = "code-assistant-v1"
    config = {"configurable": {"thread_id": "proc-001"}}

    # Turn 1 — default procedures active
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="How do I reverse a list in Python?")],
            "agent_id": agent_id,
        },
        config,
    )
    print("User: How do I reverse a list in Python?")
    print("AI (default procedures):", state["messages"][-1].content)

    # Turn 2 — trigger a live procedure update
    config2 = {"configurable": {"thread_id": "proc-002"}}
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="Please be verbose and explain everything in detail.")],
            "agent_id": agent_id,
        },
        config2,
    )
    print("\nUser: Please be verbose and explain everything in detail.")
    print("AI:", state["messages"][-1].content[:300], "...")

    # Turn 3 — new thread, updated procedures now apply automatically
    config3 = {"configurable": {"thread_id": "proc-003"}}
    state = graph.invoke(
        {
            "messages": [HumanMessage(content="How do I sort a dictionary by value?")],
            "agent_id": agent_id,
        },
        config3,
    )
    print("\nUser: How do I sort a dictionary by value?")
    print("AI (updated procedures, verbose):", state["messages"][-1].content[:400], "...")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — run all five demos in sequence
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    demo_in_context()       # Tier 1
    demo_working_memory()   # Tier 2
    demo_episodic()         # Tier 3
    demo_semantic()         # Tier 4
    demo_procedural()       # Tier 5