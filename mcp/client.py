import streamlit as st
import asyncio
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import AIMessage, HumanMessage

load_dotenv()

SYSTEM_PROMPT = """
You are a helpful expense tracking assistant.
You have access to tools to add expenses, list them, and summarise spending.
Always confirm what you've done after each action.
When the user asks about spending, use the summarise tool first.
Dates should be in YYYY-MM-DD format.
"""

async def run_agent(user_input, history):
    client = MultiServerMCPClient({
        "expense_tracker": {
            "command": "uv",
            "args": ["run", "main.py"],
            "transport": "stdio",
        }
    })
    tools = await client.get_tools()
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    messages = history + [HumanMessage(content=user_input)]
    result = await agent.ainvoke({"messages": messages})
    return result["messages"][-1].content

def run(user_input, history):
    return asyncio.run(run_agent(user_input, history))

# --- Streamlit UI ---
st.set_page_config(page_title="Expense Tracker", page_icon="💰")
st.title("💰 Expense Tracker Chat")
st.caption("Ask me to log expenses, list them, or summarise your spending.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if prompt := st.chat_input("What would you like to do?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    history = []
    for m in st.session_state.messages[:-1]:
        if m["role"] == "user":
            history.append(HumanMessage(content=m["content"]))
        else:
            history.append(AIMessage(content=m["content"]))

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            response = run(prompt, history)
        st.write(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
    