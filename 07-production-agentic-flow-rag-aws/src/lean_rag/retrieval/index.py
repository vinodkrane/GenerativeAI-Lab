"""Search index port with an Amazon OpenSearch Service implementation and an in-memory
implementation for local dev/tests.

Every read method requires an ``AccessFilter``; there is deliberately no unfiltered search.
Indexes are versioned (``chunks_v1``, ``chunks_v2`` ...) behind a live alias.
"""

from __future__ import annotations

import json
import math
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

from lean_rag.domain.models import Chunk, SearchHit
from lean_rag.security.acl import AccessFilter
from lean_rag.textutil import content_terms, stem


class SearchIndex(Protocol):
    alias: str

    def create_index(self, name: str) -> None: ...
    def index_exists(self, name: str) -> bool: ...
    def live_index(self) -> str | None: ...
    def point_alias(self, name: str) -> None: ...
    def upsert(self, chunks: list[Chunk], index: str | None = None) -> None: ...
    def delete_stale(self, document_id: str, keep_ids: set[str], index: str | None = None) -> int: ...
    def delete_document(self, document_id: str, index: str | None = None) -> int: ...
    def chunk_ids(self, document_id: str, index: str | None = None) -> set[str]: ...
    def lexical_search(
        self, query: str, acl: AccessFilter, k: int, document_id: str | None = None, index: str | None = None
    ) -> list[SearchHit]: ...
    def vector_search(
        self,
        vector: list[float],
        acl: AccessFilter,
        k: int,
        document_id: str | None = None,
        index: str | None = None,
    ) -> list[SearchHit]: ...


def index_body(dimensions: int, replicas: int) -> dict[str, Any]:
    return {
        "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": replicas}},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "document_version": {"type": "integer"},
                "ordinal": {"type": "integer"},
                "text": {"type": "text", "analyzer": "english"},
                "heading": {"type": "text", "analyzer": "english"},
                "title": {"type": "text", "analyzer": "english"},
                "keywords": {"type": "keyword"},
                "acl_principals": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "chunker_version": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "injection_suspect": {"type": "boolean"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimensions,
                    "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
                },
            },
        },
    }


def acl_filter_clause(acl: AccessFilter, document_id: str | None = None) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [
        {"term": {"tenant_id": acl.tenant_id}},
        {"terms": {"acl_principals": list(acl.principals)}},
    ]
    if document_id:
        clauses.append({"term": {"document_id": document_id}})
    return {"bool": {"filter": clauses}}


class OpenSearchIndex:
    def __init__(self, client: Any, alias: str, dimensions: int, replicas: int = 1) -> None:
        self.client = client  # opensearchpy.OpenSearch
        self.alias = alias
        self.dimensions = dimensions
        self.replicas = replicas

    def create_index(self, name: str) -> None:
        if not self.client.indices.exists(index=name):
            self.client.indices.create(index=name, body=index_body(self.dimensions, self.replicas))

    def index_exists(self, name: str) -> bool:
        return bool(self.client.indices.exists(index=name))

    def live_index(self) -> str | None:
        if not self.client.indices.exists_alias(name=self.alias):
            return None
        names = list(self.client.indices.get_alias(name=self.alias).keys())
        return names[0] if names else None

    def point_alias(self, name: str) -> None:
        actions: list[dict[str, Any]] = []
        current = self.live_index()
        if current:
            actions.append({"remove": {"index": current, "alias": self.alias}})
        actions.append({"add": {"index": name, "alias": self.alias}})
        self.client.indices.update_aliases(body={"actions": actions})  # atomic swap

    def upsert(self, chunks: list[Chunk], index: str | None = None) -> None:
        target = index or self.alias
        body: list[dict[str, Any]] = []
        for c in chunks:
            body.append({"index": {"_index": target, "_id": c.chunk_id}})
            body.append(c.model_dump())
        if not body:
            return
        resp = self.client.bulk(body=body, refresh="wait_for")
        if resp.get("errors"):
            failed = [i for i in resp.get("items", []) if i.get("index", {}).get("error")]
            raise RuntimeError(f"bulk indexing failed for {len(failed)} chunks")

    def delete_stale(self, document_id: str, keep_ids: set[str], index: str | None = None) -> int:
        query: dict[str, Any] = {
            "bool": {
                "filter": [{"term": {"document_id": document_id}}],
                "must_not": [{"ids": {"values": sorted(keep_ids)}}],
            }
        }
        resp = self.client.delete_by_query(index=index or self.alias, body={"query": query}, refresh=True)
        return int(resp.get("deleted", 0))

    def delete_document(self, document_id: str, index: str | None = None) -> int:
        return self.delete_stale(document_id, set(), index)

    def chunk_ids(self, document_id: str, index: str | None = None) -> set[str]:
        resp = self.client.search(
            index=index or self.alias,
            body={"query": {"term": {"document_id": document_id}}, "_source": False, "size": 10_000},
        )
        return {h["_id"] for h in resp["hits"]["hits"]}

    def _hits(self, resp: dict[str, Any]) -> list[SearchHit]:
        return [
            SearchHit(chunk=Chunk.model_validate(h["_source"]), score=float(h["_score"]))
            for h in resp["hits"]["hits"]
        ]

    def lexical_search(
        self, query: str, acl: AccessFilter, k: int, document_id: str | None = None, index: str | None = None
    ) -> list[SearchHit]:
        body = {
            "size": k,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["text", "heading^2", "title^2", "keywords^2"],
                            }
                        }
                    ],
                    "filter": acl_filter_clause(acl, document_id)["bool"]["filter"],
                }
            },
        }
        return self._hits(self.client.search(index=index or self.alias, body=body))

    def vector_search(
        self,
        vector: list[float],
        acl: AccessFilter,
        k: int,
        document_id: str | None = None,
        index: str | None = None,
    ) -> list[SearchHit]:
        body = {
            "size": k,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "knn": {
                    "embedding": {"vector": vector, "k": k, "filter": acl_filter_clause(acl, document_id)}
                }
            },
        }
        return self._hits(self.client.search(index=index or self.alias, body=body))


class InMemoryIndex:
    """BM25 + cosine over Python dicts, optionally persisted to a JSON file (dev/test only)."""

    def __init__(self, alias: str, path: str | Path | None = None) -> None:
        self.alias = alias
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        self._indices: dict[str, dict[str, Chunk]] = {}
        self._aliases: dict[str, str] = {}
        if self._path and self._path.exists():
            raw = json.loads(self._path.read_text())
            self._aliases = raw["aliases"]
            self._indices = {
                name: {cid: Chunk.model_validate(c) for cid, c in docs.items()}
                for name, docs in raw["indices"].items()
            }

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "aliases": self._aliases,
            "indices": {n: {cid: c.model_dump() for cid, c in d.items()} for n, d in self._indices.items()},
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self._path)

    def _resolve(self, index: str | None) -> dict[str, Chunk]:
        name = index or self._aliases.get(self.alias)
        if name is None or name not in self._indices:
            raise LookupError(f"index {index or self.alias!r} does not exist")
        return self._indices[name]

    def create_index(self, name: str) -> None:
        with self._lock:
            self._indices.setdefault(name, {})
            self._save()

    def index_exists(self, name: str) -> bool:
        return name in self._indices

    def live_index(self) -> str | None:
        return self._aliases.get(self.alias)

    def point_alias(self, name: str) -> None:
        with self._lock:
            if name not in self._indices:
                raise LookupError(name)
            self._aliases[self.alias] = name
            self._save()

    def upsert(self, chunks: list[Chunk], index: str | None = None) -> None:
        with self._lock:
            docs = self._resolve(index)
            for c in chunks:
                docs[c.chunk_id] = c
            self._save()

    def delete_stale(self, document_id: str, keep_ids: set[str], index: str | None = None) -> int:
        with self._lock:
            docs = self._resolve(index)
            stale = [cid for cid, c in docs.items() if c.document_id == document_id and cid not in keep_ids]
            for cid in stale:
                del docs[cid]
            self._save()
            return len(stale)

    def delete_document(self, document_id: str, index: str | None = None) -> int:
        return self.delete_stale(document_id, set(), index)

    def chunk_ids(self, document_id: str, index: str | None = None) -> set[str]:
        with self._lock:
            return {cid for cid, c in self._resolve(index).items() if c.document_id == document_id}

    def _candidates(self, acl: AccessFilter, document_id: str | None, index: str | None) -> list[Chunk]:
        with self._lock:
            docs = list(self._resolve(index).values())
        return [c for c in docs if acl.permits(c) and (document_id is None or c.document_id == document_id)]

    @staticmethod
    def _strip(chunk: Chunk) -> Chunk:
        return chunk.model_copy(update={"embedding": None})

    def lexical_search(
        self, query: str, acl: AccessFilter, k: int, document_id: str | None = None, index: str | None = None
    ) -> list[SearchHit]:
        docs = self._candidates(acl, document_id, index)
        if not docs:
            return []
        q_terms = {stem(t) for t in content_terms(query)}
        tokenized = [
            [
                stem(t)
                for t in content_terms(f"{c.title or ''} {c.heading or ''} {c.text} {' '.join(c.keywords)}")
            ]
            for c in docs
        ]
        n = len(docs)
        avg_len = sum(len(t) for t in tokenized) / n or 1.0
        df: Counter[str] = Counter()
        for terms in tokenized:
            df.update(set(terms))
        k1, b = 1.2, 0.75
        hits: list[SearchHit] = []
        for chunk, terms in zip(docs, tokenized, strict=True):
            tf = Counter(terms)
            score = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(terms) / avg_len))
            if score > 0:
                hits.append(SearchHit(chunk=self._strip(chunk), score=score))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:k]

    def vector_search(
        self,
        vector: list[float],
        acl: AccessFilter,
        k: int,
        document_id: str | None = None,
        index: str | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for chunk in self._candidates(acl, document_id, index):
            if chunk.embedding is None or len(chunk.embedding) != len(vector):
                continue
            score = sum(a * b for a, b in zip(chunk.embedding, vector, strict=True))
            hits.append(SearchHit(chunk=self._strip(chunk), score=score))
        hits.sort(key=lambda h: (-h.score, h.chunk.chunk_id))
        return hits[:k]
