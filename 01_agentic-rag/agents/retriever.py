import asyncio
import json
import os
from langchain_anthropic import ChatAnthropic
from .state import AgentState
from .utils import rerank_chunks, fuse_context, hyde_generate, rewrite_query, embedder

_llm_small = ChatAnthropic(model="claude-haiku-4-5")


async def _vector_search(query: str, k: int = 12) -> list:
    if embedder is None:
        print("  [retriever] Skipping vector search — no embedder configured.")
        return []

    try:
        from langchain_community.vectorstores import PGVector
        store = PGVector.from_existing_index(
            embedding=embedder,
            collection_name="kb_chunks",
            connection_string=os.getenv("DATABASE_URL_SYNC", ""),
        )
        docs = await store.asimilarity_search_with_score(query, k=k)
        return [
            {
                "content": d.page_content,
                "source": d.metadata.get("source", "unknown"),
                "score": float(s),
                "chunk_type": "vector"
            }
            for d, s in docs
        ]
    except Exception as e:
        print(f"  [retriever] Vector search failed: {e}")
        return []


async def _graph_search(query: str) -> list:
    try:
        from neo4j import AsyncGraphDatabase
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        cypher = """
        MATCH (n)
        WHERE toLower(n.description) CONTAINS toLower($keyword)
        RETURN n.name as name, n.description as context, 0.7 as score
        LIMIT 10
        """
        keyword = query.split()[0] if query else ""
        async with driver.session() as session:
            result = await session.run(cypher, {"keyword": keyword})
            records = await result.data()
        await driver.close()
        return [
            {
                "content": f"{r.get('name', '')} — {r.get('context', '')}",
                "source": "knowledge_graph",
                "score": r.get("score", 0.5),
                "chunk_type": "graph"
            }
            for r in records
        ]
    except Exception as e:
        print(f"  [retriever] Graph search failed (OK if Neo4j empty): {e}")
        return []


async def _fallback_context(query: str, llm: ChatAnthropic) -> list:
    print("  [retriever] No chunks found — using LLM-generated context as fallback.")
    response = await llm.ainvoke([
        ("system", "You are a knowledgeable assistant. Provide a factual, detailed paragraph about the topic that can serve as retrieved context."),
        ("human", f"Provide context about: {query}"),
    ])
    return [{
        "content": response.content,
        "source": "llm_fallback",
        "score": 0.6,
        "chunk_type": "vector",
    }]


async def retriever_node(state: AgentState) -> dict:
    task = state["current_task"]
    sub_query = task["sub_query"]

    if state["retrieval_round"] > 0 and state.get("critic_score"):
        failure = state["critic_score"].get("failure_reason", "")
        if failure:
            sub_query = await rewrite_query(sub_query, failure)
            print(f"  [retriever] Rewritten query: {sub_query}")

    hyde_doc = await hyde_generate(sub_query, _llm_small)

    vector_results, graph_results, hyde_results = await asyncio.gather(
        _vector_search(sub_query),
        _graph_search(sub_query),
        _vector_search(hyde_doc),
    )

    all_chunks = vector_results + graph_results + hyde_results

    if not all_chunks:
        all_chunks = await _fallback_context(sub_query, _llm_small)

    reranked = await rerank_chunks(sub_query, all_chunks, top_k=8)
    fused = await fuse_context(reranked, sub_query)

    return {
        "retrieved_chunks": reranked,
        "fused_context": fused,
        "rewritten_queries": state["rewritten_queries"] + [sub_query],
        "retrieval_round": state["retrieval_round"] + 1,
    }