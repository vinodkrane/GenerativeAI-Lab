# Import necessary modules and classes
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver # type: ignore
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
import sqlite3

# Load environment variables from a .env file
load_dotenv()

# Initialize the OpenAI chat model
llm = ChatOpenAI()

# Define the structure of the chatbot's state
class ChatState(TypedDict):
    # The state will hold a list of messages (both human and assistant)
    messages: Annotated[list[BaseMessage], add_messages]

# Define the main chat node function
def chat_node(state: ChatState):
    """
    This function represents a single node in the graph where the chatbot generates a response.
    It takes the current state (list of messages), sends it to the LLM, and returns the updated state.
    """
    messages = state['messages']  # Extract the messages from state
    response = llm.invoke(messages)  # Generate assistant response using LLM
    # Persist each assistant message by returning it in the new state
    return {"messages": [response]}

# Connect to SQLite database to persist conversations
conn = sqlite3.connect('chatbot.db', check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)  # Initialize the SQLite checkpointer

# Build the state graph for the chatbot
graph = StateGraph(ChatState)  # Graph will operate on ChatState
graph.add_node("chat_node", chat_node)  # Add a single chat node
graph.add_edge(START, "chat_node")  # Define START -> chat_node transition
graph.add_edge("chat_node", END)  # Define chat_node -> END transition

# Compile the graph into a runnable chatbot with persistence
chatbot = graph.compile(checkpointer=checkpointer)

# Utility function to retrieve all conversation threads from the database
def retrieve_all_threads():
    """
    Returns a list of all unique thread IDs stored in the SQLite checkpointer.
    """
    threads = set()
    for checkpoint in checkpointer.list(None):  # Iterate through all checkpoints
        threads.add(checkpoint.config['configurable']['thread_id'])  # Collect unique thread IDs
    return list(threads)

# Utility function to load messages for a specific conversation thread
def load_conversation(thread_id):
    """
    Given a thread ID, retrieve the full conversation as a list of dicts.
    Each dict has a 'role' (user/assistant) and 'content'.
    """
    # Load the state corresponding to this thread
    state = chatbot.get_state(config={"configurable": {"thread_id": thread_id}})
    messages = state.values.get('messages', [])  # Get messages from state
    result = []
    for msg in messages:
        # Determine role: HumanMessage -> user, else -> assistant
        role = 'user' if isinstance(msg, HumanMessage) else 'assistant'
        result.append({'role': role, 'content': msg.content})  # Add to result
    return result
