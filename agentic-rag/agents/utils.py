import asyncio
import os
from typing import List, Dict
from langchain_anthropic import ChatAnthropic

try:
    from langchain.embeddings import OpenAIEmbeddings
    embedder = OpenAIEmbeddings(model="text-embedding-3-small")
except ImportError:
    embedder = None
    print("WARNING: No embedder configured. pip install langchain-openai")


async def hyde_generate(query: str, llm: ChatAnthropic) -> str:
    response = await llm.ainvoke([
        ("system", "Write a short factual passage (3-5 sentences) that would answer the question."),
        ("human", query),
    ])
    return response.content.strip()


async def rerank_chunks(query: str, chunks: List[Dict], top_k: int = 8) -> List[Dict]:
    seen = set()
    unique = []
    for c in chunks:
        key = c["content"][:100]
        if key not in seen:
            seen.add(key)
            unique.append(c)
    sorted_chunks = sorted(unique, key=lambda x: x.get("score", 0), reverse=True)
    return sorted_chunks[:top_k]


async def fuse_context(chunks: List[Dict], query: str) -> str:
    if not chunks:
        return "No relevant context found."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[SOURCE {i} | type={c.get('chunk_type', 'unknown')} | score={c.get('score', 0):.2f}]\n{c['content']}"
        )
    return "\n\n---\n\n".join(parts)


async def rewrite_query(original: str, failure_reason: str) -> str:
    llm = ChatAnthropic(model="claude-haiku-4-5", max_tokens=200)
    response = await llm.ainvoke([
        ("system", "Rewrite the search query to fix the retrieval problem. Return ONLY the new query, nothing else."),
        ("human", f"Original query: {original}\nProblem: {failure_reason}"),
    ])
    return response.content.strip()