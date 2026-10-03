"""Builds the prompt messages for the agent.

Combines system instructions, optional RAG context,
conversation history, and the current user message.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage

from app.schemas.chat import HistoryMessage, Role

BASE_SYSTEM_INSTRUCTIONS = """You are a helpful, precise assistant for this platform.
- Use tools when they would materially improve the accuracy of your answer.
- Never fabricate tool results; only report what a tool actually returned.
- Keep answers concise and directly address the user's request.
- If you are not confident in an answer, say so rather than guessing."""


async def _fetch_rag_context(query: str) -> str | None:
    """Placeholder RAG retrieval hook.

    Wire this up to a real vector store (pgvector, Pinecone, etc.) when
    ready; the rest of the pipeline only depends on this returning a
    string of context or None.
    """
    return None


# Convert API history objects into LangChain messages.
def _history_to_messages(history: list[HistoryMessage]) -> list[AnyMessage]:
    converted: list[AnyMessage] = []
    for item in history:
        if item.role == Role.user:
            converted.append(HumanMessage(content=item.content))
        elif item.role == Role.assistant:
            converted.append(AIMessage(content=item.content))
        # system/tool history entries are intentionally not replayed verbatim
    return converted


# Build the complete message list for a single agent request.
async def build_messages(
    *,
    user_message: str,
    history: list[HistoryMessage],
    additional_context: dict | None = None,
    use_rag: bool = False,
) -> list[AnyMessage]:
    """Build the full ordered message list for a single agent turn."""
    system_sections = [BASE_SYSTEM_INSTRUCTIONS]

    if additional_context:
        # Add request-specific metadata without mixing it into user history.
        context_lines = "\n".join(f"- {k}: {v}" for k, v in additional_context.items())
        system_sections.append(f"Additional context:\n{context_lines}")

    if use_rag:
        # Retrieved context is added to the system prompt so the model
        # can use it as background knowledge for this request.
        rag_context = await _fetch_rag_context(user_message)

        if rag_context:
            system_sections.append(f"Retrieved context:\n{rag_context}")

    messages: list[AnyMessage] = [
        # Keep system instructions first so they apply to the whole turn.
        SystemMessage(content="\n\n".join(system_sections))
    ]

    # Preserve conversation flow before adding the latest user message.
    messages.extend(_history_to_messages(history))

    messages.append(HumanMessage(content=user_message))

    return messages
