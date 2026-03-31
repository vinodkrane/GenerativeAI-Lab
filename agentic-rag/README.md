# Agentic RAG from Scratch

A complete step-by-step guide to set up and run an Agentic RAG system using
LangGraph, Anthropic Claude, PostgreSQL (pgvector), and Neo4j.


## STEP 01 — INSTALL PREREQUISITES

Get Python, Docker, and system tools ready before anything else.

1. You have Python 3.14 installed on your machine.
2. You have read this article - [Next-Generation Agentic RAG with LangGraph](https://medium.com/@vinodkrane/next-generation-agentic-rag-with-langgraph-2026-edition-d1c4c068d2b8)


## STEP 02 — CREATE VIRTUAL ENVIRONMENT & INSTALL PACKAGES

Why a virtual environment?
It keeps all the packages for this project separate from your system Python.
Always do this before installing anything.

Run inside agentic-rag/ folder ---

    # 1. Create virtual environment
    python3 -m venv venv   (for mac users)
    python -m venv venv    (for windows users)

    # 2. Activate it (Mac/Linux)
    source venv/bin/activate

    # 2. Activate it (Windows)
    venv\Scripts\activate

    # You should now see (venv) at the start of your terminal prompt

    # 3. Upgrade pip first
    pip install --upgrade pip             (for mac users)
    python -m pip install --upgrade pip   (for Windows users)

Install all packages ---

    # For mac users: 
    pip install \
      langgraph==1.1.3 \
      langchain-anthropic==1.4.0 \
      langchain-community==0.3.31 \
      anthropic \
      neo4j \
      asyncpg \
      pgvector \
      psycopg2-binary \
      sqlalchemy \
      python-dotenv \
      numpy \
      aiohttp \
      langchain-openai \
      aiosqlite \
      --upgrade langgraph \
      --upgrade langgraph-checkpoint-sqlite


      # For Windows users: 
      pip install langgraph==1.1.3 langchain-anthropic==1.4.0 langchain-community==0.3.31 anthropic neo4j asyncpg pgvector psycopg2-binary sqlalchemy python-dotenv numpy aiohttp langchain-openai aiosqlite --upgrade langgraph --upgrade langgraph-checkpoint-sqlite

# STEP 03 — CONFIGURE DOCKER
Make sure you have started Docker Desktop in step 1. Now you will have to start the containers

    # Start all containers (run from agentic-rag/ folder)
    docker compose up -d

    # Wait ~30 seconds, then check status
    docker compose ps

    # Verify PostgreSQL is accepting connections
    docker exec rag_postgres pg_isready -U raguser
    # Expected output: /var/run/postgresql:5432 - accepting connections

# STEP 04 — SET UP ENVIRONMENT VARIABLES
Edit the .env file in your project root and populate following keys.

    # .env
    ANTHROPIC_API_KEY=sk-ant-...        # Your Anthropic key from Step 01
    OPENAI_API_KEY=sk-...               # Required for embeddings


# STEP 05 — RUN THE APPLICATION

With the virtual environment active and Docker running:

    python main.py


# STEP 06 — QUICK DIAGNOSTIC COMMANDS

Run these to verify your setup at any point:

    # Check Python version
    python3 --version

    # Check virtual env is active (should show venv path)
    which python

    # Check key packages are installed
    python -c "import langgraph; print('LangGraph OK:', langgraph.__version__)"
    python -c "import langchain_anthropic; print('LangChain Anthropic OK')"
    python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('API key set:', bool(os.getenv('ANTHROPIC_API_KEY')))"

    # Check Docker containers
    docker compose ps

    # Check PostgreSQL is accessible
    docker exec rag_postgres pg_isready -U raguser

    # See live logs from a container (Ctrl+C to exit)
    docker compose logs -f

# STEP 07 — TROUBLESHOOTING COMMON ERRORS


ERROR: ModuleNotFoundError: No module named 'langgraph'
CAUSE: Packages not installed or wrong venv active

FIX:   Run: source venv/bin/activate
       Then: pip install langgraph

--------------------------------------------------------------------------------

ERROR: AuthenticationError: Invalid API key
CAUSE: .env not loaded or wrong key

FIX:   Check .env has ANTHROPIC_API_KEY=sk-ant-...
       Make sure load_dotenv() is at the top of main.py

--------------------------------------------------------------------------------

ERROR: Connection refused (port 5432)
CAUSE: PostgreSQL container not running

FIX:   Run: docker compose up -d
       Wait 30 seconds and try again

--------------------------------------------------------------------------------

ERROR: Connection refused (port 7687)
CAUSE: Neo4j container not running

FIX:   Run: docker compose up -d
       Then: docker compose ps

--------------------------------------------------------------------------------

ERROR: ImportError: cannot import 'embedder'
CAUSE: OpenAI embeddings not installed

FIX:   Run: pip install langchain-openai openai
       Add OPENAI_API_KEY to .env

--------------------------------------------------------------------------------

ERROR: json.JSONDecodeError in planner
CAUSE: LLM didn't return pure JSON

FIX:   The fallback in planner_node handles this automatically —
       it will continue with a single-task plan

--------------------------------------------------------------------------------

ERROR: RuntimeError: no running event loop
CAUSE: Mixing sync and async incorrectly

FIX:   Always run through asyncio.run() in main.py —
       never call async functions directly

--------------------------------------------------------------------------------

ERROR: AttributeError: 'NoneType' on embedder
CAUSE: Embedder not configured

FIX:   For a quick start without embeddings: comment out vector search
       in retriever.py and return an empty list

--------------------------------------------------------------------------------

ERROR: Docker: Cannot connect to the Docker daemon
CAUSE: Docker Desktop not running

FIX:   Open Docker Desktop app and wait for it to fully start
       (whale icon in menu bar turns solid)

--------------------------------------------------------------------------------

ERROR: pip: command not found
CAUSE: Python not in PATH

FIX:   Use: python3 -m pip install ... instead of pip install ...

# 08 — USEFUL LINKS

  Next-Generation Agentic RAG with LangGraph (2026 Edition):  https://medium.com/@vinodkrane/next-generation-agentic-rag-with-langgraph-2026-edition-d1c4c068d2b8
