"""Two-stage retriever: wide vector search, then a cross-encoder rerank."""

from langchain_core.documents import Document
from langsmith import traceable
from sentence_transformers import CrossEncoder

from app import config
from app.vector_store import load_store


class RerankingRetriever:
    def __init__(self, fetch_k: int = config.FETCH_K, top_k: int = config.TOP_K):
        self.store = load_store()
        self.reranker = CrossEncoder(config.RERANKER_MODEL)
        self.fetch_k = fetch_k
        self.top_k = top_k

    @traceable(run_type="retriever", name="RerankingRetriever")
    def invoke(self, query: str) -> list[Document]:
        candidates = self.store.similarity_search(query, k=self.fetch_k)

        # the bi-encoder scores query and chunk separately; the cross-encoder
        # reads them together, which is slower but ranks better
        pairs = [(query, doc.page_content) for doc in candidates]
        scores = self.reranker.predict(pairs)

        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        return [doc for doc, _ in ranked[: self.top_k]]
