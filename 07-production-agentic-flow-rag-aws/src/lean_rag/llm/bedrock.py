"""Amazon Bedrock adapters: Converse for agents/generator, Titan embeddings, Bedrock Rerank."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lean_rag.llm.base import LLMResponse
from lean_rag.reliability import PermanentError, with_retries


class BedrockLLM:
    def __init__(self, client: Any) -> None:
        self.client = client  # boto3 "bedrock-runtime"

    def complete(self, *, system: str, prompt: str, model_id: str, max_tokens: int) -> LLMResponse:
        resp = self.client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
        )
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        usage = resp.get("usage", {})
        return LLMResponse(
            text=text,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            model_id=model_id,
        )


class _LRU:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._data: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> list[float] | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: str, value: list[float]) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)


class BedrockEmbedder:
    """Titan Text Embeddings V2. One text per InvokeModel call, fanned out per batch."""

    def __init__(
        self,
        client: Any,
        model_id: str,
        dimensions: int,
        batch_size: int,
        max_retries: int,
        cache_size: int = 10_000,
    ) -> None:
        self.client = client
        self.model_id = model_id
        self.dimensions = dimensions
        self.batch_size = max(1, batch_size)
        self.max_retries = max_retries
        self._cache = _LRU(cache_size)  # caches repeated query embeddings in the API process

    def _embed_one(self, text: str) -> list[float]:
        key = hashlib.sha256(f"{self.model_id}|{self.dimensions}|{text}".encode()).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        def call() -> list[float]:
            resp = self.client.invoke_model(
                modelId=self.model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True}),
            )
            payload = json.loads(resp["body"].read())
            vector = payload.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise PermanentError("embedding model returned an unexpected payload")
            return [float(x) for x in vector]

        vector = with_retries(call, op="embed", max_retries=self.max_retries)
        self._cache.put(key, vector)
        return vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with ThreadPoolExecutor(max_workers=self.batch_size) as pool:
            for start in range(0, len(texts), self.batch_size):
                out.extend(pool.map(self._embed_one, texts[start : start + self.batch_size]))
        return out


class BedrockReranker:
    def __init__(self, client: Any, model_arn: str, max_retries: int) -> None:
        self.client = client  # boto3 "bedrock-agent-runtime"
        self.model_arn = model_arn
        self.max_retries = max_retries

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[tuple[int, float]]:
        if not texts:
            return []
        resp = with_retries(
            lambda: self.client.rerank(
                queries=[{"type": "TEXT", "textQuery": {"text": query}}],
                sources=[
                    {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": t}}}
                    for t in texts
                ],
                rerankingConfiguration={
                    "type": "BEDROCK_RERANKING_MODEL",
                    "bedrockRerankingConfiguration": {
                        "numberOfResults": min(top_n, len(texts)),
                        "modelConfiguration": {"modelArn": self.model_arn},
                    },
                },
            ),
            op="rerank",
            max_retries=self.max_retries,
        )
        return [(int(r["index"]), float(r["relevanceScore"])) for r in resp.get("results", [])]
