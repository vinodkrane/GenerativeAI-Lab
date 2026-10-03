# LangSmith Observability Project

This project demonstrates **LangSmith's observability and debugging capabilities** for LangChain pipelines using `langsmith`, `langchain`, and `OpenAI` integrations.

You’ll explore:

- Tracing basic LLM calls
- Observing multi-step pipelines
- Simulating failures and implementing fallbacks & retry logic

---

## Prerequisites

- Python 3.9+
- [LangSmith](https://smith.langchain.com/) account
- [OpenAI API Key](https://platform.openai.com/account/api-keys)

---

## Setup Guide

### 1. Clone the Repository

```bash
git clone https://github.com/vinodkrane/GenerativeAI-Lab.git
cd langsmith
```

### 2. Create & Activate a Virtual Environment
```bash
python -m venv venv

# Activate on Mac/Linux:
source venv/bin/activate

# Activate on Windows:
venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

## Environment Configuration
Create a .env file in the root directory:
```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=<your-langsmith-api-key>
OPENAI_API_KEY=<your-openai-api-key>
```

Replace <your-langsmith-api-key> and <your-openai-api-key> with your actual keys.


## Running the code
```bash
python 01_observing_llm_calls.py
```

## Observing in LangSmith
All runs can be viewed on your [LangSmith](https://smith.langchain.com/) dashboard.

## Tips
Keep .env in your .gitignore to avoid exposing credentials.