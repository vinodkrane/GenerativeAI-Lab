from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

# Initialize the LLM (OpenAI Chat model)
llm = ChatOpenAI()

# Define the structure of the chat state
class ChatState(TypedDict):
    # The chat state will store a list of messages (conversation history).
    # `Annotated` with `add_messages` means new messages will be appended automatically.
    messages: Annotated[list[BaseMessage], add_messages]

# Define a node function for handling the chat interaction
def chat_node(state: ChatState) -> ChatState:
    # Retrieve messages from the state (conversation history)
    messages = state.get("messages", [])

    # Pass messages to the LLM and get a response
    response = llm.invoke(messages)

    # Append LLM response to the conversation
    messages.append(response)
    
    # Return updated state with new message
    return {"messages": messages}

# Create a memory checkpointer to persist conversation state between runs
checkpointer = MemorySaver()

# Build the graph for conversation flow
graph = StateGraph(ChatState)

# Add a single node that handles chatting
graph.add_node("chat_node", chat_node)

# Define edges: START → chat_node → END
graph.add_edge(START, "chat_node")
graph.add_edge("chat_node", END)

# Compile the chatbot with memory checkpointing
chatbot = graph.compile(checkpointer=checkpointer)