"""Hybrid retrieval: BM25 + kNN, fused with Reciprocal Rank Fusion, then reranked.

Fusion is done in code rather than an OpenSearch search pipeline so the behaviour is the
same against OpenSearch and the in-memory index, and is unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

from lean_rag.config import Limits
from lean_rag.domain.models import SearchHit
from lean_rag.llm.base import Embedder, Reranker
from lean_rag.observability.logging import log_event
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import with_retries
from lean_rag.retrieval.index import SearchIndex
from lean_rag.security.acl import AccessFilter

logger = logging.getLogger(__name__)
RRF_K = 60


def rrf_fuse(ranked_lists: list[list[SearchHit]], k: int = RRF_K) -> list[SearchHit]:
    scores: dict[str, float] = {}
    best: dict[str, SearchHit] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            cid = hit.chunk.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            best.setdefault(cid, hit)
    fused = [best[cid].model_copy(update={"score": s}) for cid, s in scores.items()]
    fused.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
    return fused


class HybridRetriever:
    def __init__(
        self, index: SearchIndex, embedder: Embedder, reranker: Reranker, limits: Limits, metrics: Metrics
    ) -> None:
        self.index = index
        self.embedder = embedder
        self.reranker = reranker
        self.limits = limits
        self.metrics = metrics
        self._live_seen = False  # once a live alias exists it is never removed (only swapped)

    def _retry[T](self, op: str, fn: Callable[[], T]) -> T:
        return with_retries(
            fn,
            op=op,
            max_retries=self.limits.max_retries,
            base_delay=self.limits.retry_base_delay_s,
            max_delay=self.limits.retry_max_delay_s,
        )

    def search(
        self,
        queries: list[str],
        acl: AccessFilter,
        *,
        k: int | None = None,
        document_id: str | None = None,
        index: str | None = None,
        use_lexical: bool = True,
        use_vector: bool = True,
    ) -> list[SearchHit]:
        k = k or self.limits.retrieval_candidates
        if index is None and not self._has_live_index():
            return []  # nothing ingested yet: no index exists to search
        queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q][
            : self.limits.max_rewritten_queries
        ]
        ranked: list[list[SearchHit]] = []
        vectors: list[list[float]] = []
        if use_vector and queries:
            vectors = self._retry("embed_query", lambda: self.embedder.embed(queries))
        for i, query in enumerate(queries):
            if use_lexical:
                ranked.append(
                    self._retry(
                        "lexical_search",
                        partial(self.index.lexical_search, query, acl, k, document_id, index),
                    )
                )
            if use_vector:
                ranked.append(
                    self._retry(
                        "vector_search",
                        partial(self.index.vector_search, vectors[i], acl, k, document_id, index),
                    )
                )
            self.metrics.put("ToolCalls", int(use_lexical) + int(use_vector), Tool="search")
        fused = rrf_fuse(ranked)
        return self._enforce_acl(fused, acl)

    def _has_live_index(self) -> bool:
        if not self._live_seen:
            self._live_seen = self.index.live_index() is not None
        return self._live_seen

    def rerank(self, question: str, hits: list[SearchHit], top_n: int | None = None) -> list[SearchHit]:
        top_n = top_n or self.limits.rerank_top_n
        if not hits:
            return []
        order = self._retry(
            "rerank", lambda: self.reranker.rerank(question, [h.chunk.text for h in hits], top_n)
        )
        self.metrics.put("ToolCalls", 1, Tool="rerank")
        return [hits[i].model_copy(update={"score": score}) for i, score in order]

    def _enforce_acl(self, hits: list[SearchHit], acl: AccessFilter) -> list[SearchHit]:
        """Defence in depth: the index already filtered; verify again in code."""
        allowed = [h for h in hits if acl.permits(h.chunk)]
        if len(allowed) != len(hits):
            self.metrics.put("AclViolationsBlocked", len(hits) - len(allowed))
            log_event(logger, "acl_post_filter_blocked_hits", logging.ERROR, blocked=len(hits) - len(allowed))
        return allowed
