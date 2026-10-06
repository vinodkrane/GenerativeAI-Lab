"""Deterministic, dependency-free embedder and reranker for local development, tests and
offline evaluation. They are honest lexical models, not stand-ins that pretend to be Bedrock:
quality is lower, behaviour is reproducible.
"""

from __future__ import annotations

import hashlib
import math

from lean_rag.textutil import content_terms, coverage, stem


class HashingEmbedder:
    """Feature-hashed bag of stemmed unigrams + bigrams, L2-normalised."""

    def __init__(self, dimensions: int = 384) -> None:
        self.model_id = f"local-hashing-{dimensions}"
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        terms = [stem(t) for t in content_terms(text)]
        features = terms + [f"{a}_{b}" for a, b in zip(terms, terms[1:], strict=False)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class LexicalReranker:
    """Scores by query-term coverage, with a small bonus for phrase adjacency."""

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[tuple[int, float]]:
        q_terms = [stem(t) for t in content_terms(query)]
        bigrams = set(zip(q_terms, q_terms[1:], strict=False))
        scored: list[tuple[int, float]] = []
        for i, text in enumerate(texts):
            t_terms = [stem(t) for t in content_terms(text)]
            phrase = len(bigrams & set(zip(t_terms, t_terms[1:], strict=False))) / max(1, len(bigrams))
            scored.append((i, round(0.8 * coverage(query, text) + 0.2 * phrase, 6)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_n]
