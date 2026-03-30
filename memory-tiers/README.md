# LangGraph Memory Tiers Demo

This project demonstrates **all five memory tiers** in LangGraph (v1.1.x), each implemented as a self-contained runnable example.

The goal is to show how agents can evolve from simple in-context conversations to full long-term, structured, and procedural memory systems.

---

## Memory Tiers Covered

### 1. In-Context Memory

* Conversation history stored in state
* Uses `add_messages` reducer
* No persistence beyond a single call
* Best for short chats

### 2. Working Memory

* Scratchpad for task progress
* Tracks goals, steps, and reasoning
* Uses `MemorySaver` checkpointer
* Persists within a task session

### 3. Episodic Memory

* Stores past conversation events
* Retrieved via similarity search
* Uses `InMemoryStore`
* Enables recalling past interactions

### 4. Semantic Memory

* Structured long-term facts
* Stores user preferences and profile
* Key-value storage in `InMemoryStore`
* Used for personalization

### 5. Procedural Memory

* Agent behavioural rules
* Stored as system prompt instructions
* Can be updated dynamically
* Defines agent “muscle memory”

---

## Installation

```bash
pip3 install requirements.txt
```

---

## Environment Variable

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

---

## Running the Demo

Run the script:

```bash
python main.py
```

This executes all five tiers sequentially:

```
Tier 1 — In-Context Memory
Tier 2 — Working Memory
Tier 3 — Episodic Memory
Tier 4 — Semantic Memory
Tier 5 — Procedural Memory
```

## What Each Demo Shows

| Tier | Memory Type | Persistence | Use Case                 |
| ---- | ----------- | ----------- | ------------------------ |
| 1    | In-context  | None        | Simple chat              |
| 2    | Working     | Session     | Task solving             |
| 3    | Episodic    | Long-term   | Past conversation recall |
| 4    | Semantic    | Long-term   | User preferences         |
| 5    | Procedural  | Long-term   | Agent behaviour rules    |

---

## Architecture Overview

```
User Input
     │
     ▼
LangGraph State
     │
     ├── In-context messages
     ├── Working scratchpad
     ├── Episodic store
     ├── Semantic store
     └── Procedural instructions
     │
     ▼
LLM Response
```

---

## When to Use Each Memory

* Use **In-Context** for short conversations
* Use **Working** for multi-step reasoning
* Use **Episodic** for remembering past sessions
* Use **Semantic** for structured facts
* Use **Procedural** for agent behaviour rules

---


## Requirements

* Python 3.9+
* LangGraph 1.1+
* OpenAI API key

---

## Result

You now have a **full cognitive memory stack** for building advanced AI agents using LangGraph.

Happy building! 🚀
