"""Retrieve -> rerank -> generate, returned in a shape the evals can score."""

from langsmith import traceable

from app import config
from app.generator import generate
from app.retriever import RerankingRetriever


class RagPipeline:
    def __init__(self, fetch_k: int = config.FETCH_K, top_k: int = config.TOP_K):
        self.retriever = RerankingRetriever(fetch_k=fetch_k, top_k=top_k)

    @traceable(run_type="chain", name="RagPipeline")
    def invoke(self, query: str) -> dict:
        docs = self.retriever.invoke(query)
        context = [doc.page_content for doc in docs]
        answer = generate(query, context)
        return {"query": query, "context": context, "answer": answer}


if __name__ == "__main__":
    result = RagPipeline().invoke("Why do we need golden datasets?")
    print("Q:", result["query"])
    print("A:", result["answer"])
    for i, chunk in enumerate(result["context"]):
        print(f"  [{i}] {chunk[:120]}...")
