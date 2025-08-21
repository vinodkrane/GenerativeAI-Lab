# LangGraph Chatbot with Persistent Memory

This project implements a chatbot using LangGraph, LangChain, and OpenAI's GPT-based models. The chatbot has persistent memory to retain conversation history, allowing for a more seamless user experience across sessions. The frontend is built with Streamlit, providing a simple and intuitive interface for users to interact with the chatbot.

## Features
- **Persistent Memory**: The chatbot remembers conversations between sessions.
- **Conversation Threads**: Users can create and switch between multiple chat threads.
- **Real-time Interaction**: The chatbot generates responses in real-time with OpenAI's GPT model.
- **Customizable UI**: Built using Streamlit with a sidebar to manage conversations.

## Technologies
- **LangGraph**: Manages conversation state and flow.
- **LangChain**: Handles integration with the OpenAI API.
- **OpenAI GPT**: Powers the conversation with an AI language model.
- **Streamlit**: Provides the web interface for the chatbot.
- **Python**: Main programming language for backend logic.

## Requirements

1. **Python 3.8+** or later
2. **Install dependencies**:
    Create a `requirements.txt` file with the following content to specify the necessary packages:

    ```txt
    langchain==0.15.0
    langgraph==0.1.0
    openai==0.27.0
    streamlit==1.19.0
    ```

3. **Install dependencies using `pip`**:
    If you don’t have a `requirements.txt` file yet, create one with the content above and install the necessary dependencies with the following command:

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

### 2. Run the Application

To start the Streamlit interface and run the chatbot:

```bash
streamlit run langgraph_frontend.py
