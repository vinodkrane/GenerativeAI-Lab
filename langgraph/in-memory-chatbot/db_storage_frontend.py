import streamlit as st
from db_storage_backend import chatbot, retrieve_all_threads, load_conversation
from langchain_core.messages import HumanMessage
import uuid

# **************************************** Utility Functions *************************

def generate_thread_id():
    """
    Generate a unique thread ID using UUID.
    Each chat session will have a unique identifier.
    """
    return str(uuid.uuid4())

def reset_chat():
    """
    Reset the current chat session:
    - Generate a new thread ID
    - Add the thread to session_state
    - Clear the message history
    """
    thread_id = generate_thread_id()
    st.session_state['thread_id'] = thread_id
    add_thread(thread_id)
    st.session_state['message_history'] = []

def add_thread(thread_id):
    """
    Add a thread ID to the list of chat threads if it doesn't already exist.
    Also initialize a friendly name for display in the sidebar.
    """
    if thread_id not in st.session_state['chat_threads']:
        st.session_state['chat_threads'].append(thread_id)
        st.session_state['chat_names'][thread_id] = f"Conversation {len(st.session_state['chat_threads'])}"

# **************************************** Session Setup ***************************

# Initialize message history if not already present
if 'message_history' not in st.session_state:
    st.session_state['message_history'] = []

# Initialize thread ID for current session
if 'thread_id' not in st.session_state:
    st.session_state['thread_id'] = generate_thread_id()

# Load all previous threads from backend if not already present
if 'chat_threads' not in st.session_state:
    st.session_state['chat_threads'] = retrieve_all_threads()

# Map thread_id -> user-friendly name
if 'chat_names' not in st.session_state:
    st.session_state['chat_names'] = {}
    for idx, tid in enumerate(st.session_state['chat_threads'], start=1):
        st.session_state['chat_names'][tid] = f"Conversation {idx}"

# Ensure current thread is included in session_state
add_thread(st.session_state['thread_id'])

# **************************************** Sidebar UI ******************************

st.sidebar.title('LangGraph Chatbot')

# Button to start a new chat
if st.sidebar.button('New Chat'):
    reset_chat()

st.sidebar.header('My Conversations')

# Display a list of previous conversation threads in the sidebar
for thread_id in st.session_state['chat_threads'][::-1]:
    # Show friendly name instead of raw thread_id
    display_name = st.session_state['chat_names'].get(thread_id, str(thread_id))
    if st.sidebar.button(display_name):
        # Switch to selected conversation
        st.session_state['thread_id'] = thread_id
        st.session_state['message_history'] = load_conversation(thread_id)

# **************************************** Main UI *********************************

# Display previous messages in chat
for message in st.session_state['message_history']:
    with st.chat_message(message['role']):
        st.text(message['content'])

# Input box for user to type new message
user_input = st.chat_input('Type here')

if user_input:
    # Add user message to message history
    st.session_state['message_history'].append({'role': 'user', 'content': user_input})
    with st.chat_message('user'):
        st.text(user_input)

    # Configuration for chatbot (includes thread ID)
    CONFIG = {
        "configurable": {"thread_id": st.session_state["thread_id"]},
        "metadata": {"thread_id": st.session_state["thread_id"]},
        "run_name": "chat_turn",
    }

    # Stream assistant response from the chatbot
    with st.chat_message('assistant'):
        ai_message = st.write_stream(
            msg_chunk.content for msg_chunk, metadata in chatbot.stream(
                {'messages': [HumanMessage(content=user_input)]},
                config=CONFIG,
                stream_mode='messages'  # Stream messages chunk by chunk
            )
        )
    
    # Add assistant message to message history
    st.session_state['message_history'].append({'role': 'assistant', 'content': ai_message})
