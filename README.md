# GenerativeAI-Lab

This repo is a hands-on lab for building real GenAI systems, not just toy prompts.

It covers:
- agentic RAG pipelines with planning, retrieval, critique, and memory
- LangGraph chatbots with short-term and persistent memory
- LangSmith tracing/debugging workflows
- MCP server/client patterns for tool-enabled assistants
- practical memory architecture patterns for agents

Most folders are independent mini-projects, so you can pick one topic and run it directly.

## What each top-level folder is about

### agentic-rag/
An end-to-end Agentic RAG pipeline built with LangGraph.

What it demonstrates:
- graph-based orchestration across planner, retriever, tool agent, reasoner, critic, formatter, and memory nodes
- iterative answer improvement loop using a critic pass
- episodic memory persisted to JSON and checkpointing via SQLite
- optional infra via Docker Compose (Postgres + Neo4j)

Key files:
- `agentic-rag/main.py`: runs a sample query and streams node-level progress
- `agentic-rag/graph.py`: graph wiring and routing logic
- `agentic-rag/agents/`: each agent node implementation
- `agentic-rag/docker-compose.yml`: local service dependencies

Quick run:
```bash
cd agentic-rag
python main.py
```

### langgraph/in-memory-chatbot/
A Streamlit chatbot project showing two storage styles for conversation memory.

What it demonstrates:
- in-memory sessions (fast, ephemeral)
- SQLite-backed persistent threads
- multi-conversation sidebar UX in Streamlit

Key files:
- `langgraph/in-memory-chatbot/langgraph_frontend.py`: in-memory UI
- `langgraph/in-memory-chatbot/langgraph_backend.py`: in-memory graph backend
- `langgraph/in-memory-chatbot/db_storage_frontend.py`: persistent-memory UI
- `langgraph/in-memory-chatbot/db_storage_backend.py`: SQLite-backed backend

Quick run:
```bash
cd langgraph/in-memory-chatbot
pip install -r requirements.txt
streamlit run langgraph_frontend.py
# or
streamlit run db_storage_frontend.py
```

### langsmith/
Short scripts focused on observability and debugging with LangSmith.

What it demonstrates:
- tracing a single LLM call
- tracing a multi-step chain/pipeline
- debugging and retry/fallback behavior

Key files:
- `langsmith/01_observing_llm_calls.py`
- `langsmith/02_complex_pipeline_tracing.py`
- `langsmith/03_debugging_and_retry.py`

Quick run:
```bash
cd langsmith
pip install -r requirements.txt
python 01_observing_llm_calls.py
```

### mcp/
A practical MCP example: expense tracker tools exposed by a FastMCP server, plus a Streamlit client using a ReAct agent.

What it demonstrates:
- building MCP tools (`add_expense`, `list_expenses`, `summarize`)
- exposing a JSON resource (`expense://categories`)
- connecting an MCP server to an LLM agent with `langchain-mcp-adapters`
- local persistence via SQLite

Key files:
- `mcp/main.py`: FastMCP server + tool/resource definitions
- `mcp/client.py`: Streamlit chat client that calls MCP tools through an agent
- `mcp/categories.json`: category/subcategory taxonomy

Quick run:
```bash
cd mcp
uv run mcp dev main.py
# in another terminal
streamlit run client.py
```

### memory-tiers/
A single script that walks through all major memory tiers for agents.

What it demonstrates:
- in-context memory
- working memory (scratchpad + checkpoints)
- episodic memory (past interaction retrieval)
- semantic memory (structured user facts)
- procedural memory (behavior rules)

Key files:
- `memory-tiers/memory-tiers.py`: all demos in one place

Quick run:
```bash
cd memory-tiers
pip install -r requirements.txt
python memory-tiers.py
```

## Repo structure at a glance

```text
GenerativeAI-Lab/
├── agentic-rag/                  # Agentic RAG graph + infra
├── langgraph/in-memory-chatbot/  # Streamlit chatbot variants
├── langsmith/                    # Tracing and debugging scripts
├── mcp/                          # MCP server/client expense tracker
└── memory-tiers/                 # Memory architecture demos
```

## Common setup notes

- Python 3.9+ is fine for most folders, but `mcp/` and `agentic-rag/` docs currently target newer Python versions.
- You will need API keys depending on the project (`OPENAI_API_KEY`, sometimes `ANTHROPIC_API_KEY`, `LANGCHAIN_API_KEY`).
- Folder READMEs include deeper setup details and troubleshooting.

## Who this repo is for

Use this repo if you are:
- learning how to move from simple chat apps to agentic systems
- comparing memory strategies across implementations
- trying observability tooling before shipping production flows
- experimenting with MCP-based tool integration locally
