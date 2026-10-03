# llm-platform

Production-grade FastAPI + LangChain + LangGraph service implementing:

```
Client -> FastAPI -> Middleware (CORS, RequestID, Logging, Auth, RateLimit)
       -> Request Validation (Pydantic)
       -> State Manager (Redis short-term + Postgres durable log)
       -> Prompt Builder (system + history + context + optional RAG)
       -> Guardrails (input)
       -> LLM Agent (LangGraph ReAct loop: model <-> tools)
       -> Guardrails (output)
       -> Typed FastAPI Response
```

## Quick start

```bash
# 1. Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install dependencies (creates .venv + uv.lock)
uv sync

# 3. Copy the environment template and fill in real secrets
cp .env .env.local   # or just edit .env directly
#   - OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY (whichever MODEL_PROVIDER you use)
#   - JWT_SECRET, API_KEY - change these before deploying anywhere real
#   - REDIS_URL, POSTGRES_URL - point at a real Redis + Postgres instance

# 4. Start Redis + Postgres locally, e.g.:
docker run -d -p 6379:6379 redis:7
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=password -e POSTGRES_USER=user -e POSTGRES_DB=llmdb postgres:16

# 5. Run the API
uv run uvicorn app.main:app --reload
```

The app will refuse to serve requests to `/chat` until Redis and Postgres are
reachable (both are connected in the FastAPI `lifespan`, see `app/main.py`).
`/health` is always public and doesn't touch either store.

## Calling the API

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-me-dev-api-key" \
  -d '{
        "session_id": "sess-1",
        "user_id": "user-1",
        "message": "What is 12 * (4 + 1), and what is the weather in Sheffield?"
      }'
```

Auth accepts either:
- `X-API-Key: <API_KEY from .env>` (service-to-service), or
- `Authorization: Bearer <JWT>` signed with `JWT_SECRET` (see `app/core/security.py:create_access_token` to mint one for testing).

## Project layout

Matches the target structure you specified:

- `app/core/` - settings, structlog config, JWT/API-key auth helpers, exception hierarchy
- `app/middleware/` - auth, request-id, access-logging, rate limiting (slowapi), CORS
- `app/schemas/chat.py` - `ChatRequest` / `ChatResponse` / tool-call records
- `app/services/` - `state_manager` (Redis + Postgres), `prompt_builder`, `chat_service` (orchestration)
- `app/ai/` - `models.py` (provider factory), `tools.py` (Pydantic-schema'd tools), `guardrails.py`
  (input/output policy checks), `agent.py` (LangGraph `create_react_agent`), `graph.py` (outer
  guardrail-in -> agent -> guardrail-out `StateGraph`)
- `app/db/` - async Postgres (SQLAlchemy 2.0 + asyncpg) and Redis clients
- `tests/` - guardrail unit tests + endpoint tests (LLM graph mocked out, no network calls)

## Extending it

- **Add a tool**: define a Pydantic `args_schema` + function in `app/ai/tools.py`, add it to the
  list returned by `build_tools()`. Anything sensitive (like `user_id`) should stay a closure
  variable, not a schema field the LLM can set itself.
- **Wire up real RAG**: implement `_fetch_rag_context` in `app/services/prompt_builder.py` against
  your vector store; the rest of the pipeline is already threaded to use it when
  `ChatRequest.use_rag=true`.
- **Add a guardrail**: extend `check_input` / `check_output` in `app/ai/guardrails.py` - e.g. call
  a moderation API instead of/alongside the current regex rules.
- **Structured final answers**: pass `response_format=<a Pydantic model>` to
  `create_react_agent(...)` in `app/ai/agent.py` if you want the LLM's last turn coerced into a
  specific schema rather than free text.

## Tests & linting

```bash
make test    # uv run pytest -v
make lint    # uv run ruff check .
make format  # uv run ruff format .
```

The test suite mocks `chat_graph.ainvoke` and the state manager, so it runs with no live LLM
provider, Redis, or Postgres required.

## Notes on the provided setup

- Your `pip list` showed **Python 3.12.2** installed, even though 3.14 was mentioned - `.python-version`
  is pinned to `3.12` to match what's actually on your machine; `uv sync` will use that.
- The exact pinned versions in `pyproject.toml` (langchain 1.3.11, langgraph 1.2.7, etc.) are
  copied from your spec - `uv sync` will resolve/install whatever is actually available for those
  pins on PyPI at install time.
