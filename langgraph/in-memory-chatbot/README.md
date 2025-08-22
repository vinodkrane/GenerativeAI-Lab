# LangGraph Chatbot with In-Memory and Persistent Storage

This project implements a chatbot using LangGraph, LangChain, and OpenAI's GPT models, with flexible memory support. You can run the chatbot in either:

- In-memory mode for quick, temporary sessions

- Database-backed mode for persistent memory across sessions

The frontend is built with Streamlit, providing a clean, intuitive UI where users can interact with the bot and manage multiple chat threads.

## Features
- **Flexible Memory Options**: Choose between in-memory (ephemeral) or database-backed (persistent) storage
- **Conversation Threads**: Users can create and switch between multiple chat threads.
- **Real-time Interaction**: The chatbot generates responses in real-time with OpenAI's GPT model.
- **Customizable UI**: Built using Streamlit with a sidebar to manage conversations.

## Technologies
- **LangGraph**: Manages conversation state and flow.
- **LangChain**: Handles integration with the OpenAI API.
- **OpenAI GPT**: Powers the conversation with an AI language model.
- **Streamlit**: Provides the web interface for the chatbot.
- **Python**: Main programming language
- **Memory**: MemorySaver for in-memory chatbot, SqliteSaver for db backed chatbot

## Requirements

1. **Python 3.8+** or later
2. **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Setup

### 1. Set up OpenAI API Key
You’ll need an OpenAI API key to interact with the model. Set up your API key by either:

- **Adding it to your environment variables**:
    ```bash
    export OPENAI_API_KEY="your-openai-api-key"
    ```

- **Or, directly in the code (not recommended for production)**:
    ```python
    import openai
    openai.api_key = "your-openai-api-key"
    ```

### 2. Run the chatbot with in-memory storage

To start the Streamlit interface and run the chatbot:

```bash
streamlit run langgraph_frontend.py
```

### 3. Run the chatbot with database-backed storage

To start the Streamlit interface and run the chatbot:

```bash
streamlit run db_storage_frontend.py
```
