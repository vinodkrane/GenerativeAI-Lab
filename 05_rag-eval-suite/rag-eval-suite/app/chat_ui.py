"""Streamlit chat UI. Run from the project root: streamlit run app/chat_ui.py"""

import sys
import time
from pathlib import Path

# streamlit puts app/ on sys.path, not the project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app import config
from app.pipeline import RagPipeline

ABSTENTION = config.ABSTAIN_MESSAGE

st.set_page_config(page_title="LLM Evals TA", page_icon="🎓", layout="centered")


# loading the store and the cross-encoder is slow, so build once per setting
@st.cache_resource(show_spinner="Loading retriever and reranker…")
def get_pipeline(fetch_k: int, top_k: int) -> RagPipeline:
    return RagPipeline(fetch_k=fetch_k, top_k=top_k)


with st.sidebar:
    st.header("Retrieval settings")
    fetch_k = st.slider(
        "fetch_k: candidates from the vector store",
        min_value=5,
        max_value=30,
        value=10,
        help="How many chunks the bi-encoder over-retrieves before reranking.",
    )
    top_k = st.slider(
        "top_k: chunks kept after reranking",
        min_value=1,
        max_value=10,
        value=5,
        help="How many chunks the cross-encoder keeps and feeds to the generator.",
    )
    if top_k > fetch_k:
        st.warning("top_k is larger than fetch_k — you'll get at most fetch_k chunks.")

    st.divider()
    show_context = st.toggle("Show retrieved context", value=True)
    if st.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.caption(
        "Answers are grounded only in the course transcripts. If the material "
        "doesn't cover it, the assistant abstains."
    )


st.title("🎓 LLM Evals — Course TA")
st.caption("Ask anything about the course transcripts. Answers come only from the material.")

if "messages" not in st.session_state:
    st.session_state.messages = []


def render_context(context: list[str], latency: float | None = None) -> None:
    """Show the retrieved chunks behind an answer in a collapsed expander."""
    label = f"📚 {len(context)} retrieved chunk{'s' if len(context) != 1 else ''}"
    if latency is not None:
        label += f" · {latency:.1f}s"
    with st.expander(label):
        for i, chunk in enumerate(context, start=1):
            st.markdown(f"**Chunk {i}**")
            st.text(chunk)
            if i != len(context):
                st.divider()


# replay the conversation so far
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and show_context and message.get("context"):
            render_context(message["context"], message.get("latency"))


query = st.chat_input("e.g. What is the difference between online and offline eval?")

if query:
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        try:
            rag = get_pipeline(fetch_k, top_k)
            with st.spinner("Retrieving and generating…"):
                started = time.perf_counter()
                result = rag.invoke(query)
                latency = time.perf_counter() - started
        except Exception as exc:  # bad API key, missing store, model download, …
            st.error(f"Something went wrong: {exc}")
            st.session_state.messages.pop()  # drop the unanswered turn
        else:
            answer, context = result["answer"], result["context"]
            st.markdown(answer)
            if answer.strip() == ABSTENTION:
                st.info("The assistant abstained — this isn't covered in the transcripts.")
            if show_context:
                render_context(context, latency)

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                    "context": context,
                    "latency": latency,
                }
            )
