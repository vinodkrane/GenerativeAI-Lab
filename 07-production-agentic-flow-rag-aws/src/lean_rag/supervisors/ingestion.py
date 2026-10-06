"""Ingestion supervisor: a deterministic state machine driving one document to a terminal state.

Each state has one handler. Handlers are idempotent and persist their output to S3 under the
document's derived prefix, so a redelivered SQS message resumes from the stored state
instead of starting over. Agents are consulted for decisions (chunk plan, probes, re-chunk
recommendation); every action - parsing, writing, indexing, status changes - is code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from lean_rag.agents.base import AgentRun
from lean_rag.agents.chunking_agent import ChunkingAgent
from lean_rag.agents.verifier_agent import Probe, VerifierAgent, sample_chunks
from lean_rag.config import AGENT_POLICY_VERSION, CHUNKER_VERSION, Settings
from lean_rag.domain.models import Chunk, DocStatus, Document, derived_prefix
from lean_rag.domain.state_machine import IN_PROGRESS, TERMINAL
from lean_rag.ingestion.chunking import ChunkPlan, Strategy, build_chunks
from lean_rag.ingestion.parsing import FileKind, ParsedDocument, TextractOcr, parse
from lean_rag.llm.base import Embedder
from lean_rag.observability.logging import log_context, log_event
from lean_rag.observability.metrics import Metrics
from lean_rag.reliability import Budget, BudgetExceeded, PermanentError, TransientError, with_retries
from lean_rag.retrieval.hybrid import HybridRetriever
from lean_rag.retrieval.index import SearchIndex
from lean_rag.security.acl import AccessFilter
from lean_rag.storage.db import Repository
from lean_rag.storage.objects import ObjectStore

logger = logging.getLogger(__name__)
PROBE_TOP_K = 5


@dataclass(frozen=True)
class Outcome:
    document_id: str
    status: DocStatus
    detail: str | None = None


class IngestionSupervisor:
    def __init__(
        self,
        *,
        settings: Settings,
        repo: Repository,
        objects: ObjectStore,
        index: SearchIndex,
        embedder: Embedder,
        retriever: HybridRetriever,
        chunking_agent: ChunkingAgent,
        verifier_agent: VerifierAgent,
        metrics: Metrics,
        ocr: TextractOcr | None,
    ) -> None:
        self.settings = settings
        self.limits = settings.limits
        self.repo = repo
        self.objects = objects
        self.index = index
        self.embedder = embedder
        self.retriever = retriever
        self.chunking_agent = chunking_agent
        self.verifier_agent = verifier_agent
        self.metrics = metrics
        self.ocr = ocr
        self._handlers: dict[
            DocStatus, Callable[[Document, AgentRun], tuple[DocStatus, dict[str, object]]]
        ] = {
            DocStatus.RECEIVED: self._on_received,
            DocStatus.PARSING: self._on_parsing,
            DocStatus.CHUNKING: self._on_chunking,
            DocStatus.EMBEDDING: self._on_embedding,
            DocStatus.INDEXING: self._on_indexing,
            DocStatus.VERIFYING: self._on_verifying,
        }

    # --- entry points ---------------------------------------------------------------------
    def handle_object_created(self, tenant_id: str, document_id: str, version: int) -> Outcome | None:
        """Called for each S3 ObjectCreated event. Safe to call any number of times."""
        doc = self.repo.get(document_id)
        if doc is None or doc.tenant_id != tenant_id:
            log_event(logger, "event_for_unknown_document", logging.WARNING, document_id=document_id)
            return None
        if version != doc.version:
            log_event(logger, "stale_version_event", event_version=version, current_version=doc.version)
            return None
        if doc.status is DocStatus.PENDING_UPLOAD:
            self.repo.transition(
                doc.document_id, DocStatus.PENDING_UPLOAD, DocStatus.RECEIVED, "upload received"
            )
        return self.process(document_id)

    def process(self, document_id: str) -> Outcome:
        """Advance the document until it reaches a terminal state (or a transient error is raised)."""
        started = time.perf_counter()
        run = AgentRun(
            budget=Budget(self.limits.max_llm_calls_per_ingestion, self.limits.max_tokens_per_ingestion),
            agent_run_id=uuid.uuid4().hex,
        )
        doc = self._require(document_id)
        with log_context(document_id=doc.document_id, tenant_id=doc.tenant_id, agent_run_id=run.agent_run_id):
            if doc.status not in IN_PROGRESS and doc.status is not DocStatus.RECEIVED:
                return Outcome(doc.document_id, doc.status, "nothing to do")
            self.repo.update_fields(doc.document_id, attempts=doc.attempts + 1)
            for _ in range(self.limits.max_ingestion_steps):
                doc = self._require(document_id)
                if doc.status in TERMINAL:
                    break
                handler = self._handlers.get(doc.status)
                if handler is None:
                    raise PermanentError(f"no handler for state {doc.status}")
                try:
                    target, fields = handler(doc, run)
                except (PermanentError, BudgetExceeded) as exc:
                    self._fail(doc, str(exc))
                    break
                except TransientError:
                    self.metrics.put("IngestionTransientErrors", 1, Stage=doc.status.value)
                    raise
                step_detail = str(fields.pop("_detail", "")) or None
                if not self.repo.transition(doc.document_id, doc.status, target, step_detail, **fields):
                    raise TransientError("document state changed concurrently; will retry")
                log_event(logger, "transition", from_status=doc.status.value, to_status=target.value)
            else:
                doc = self._require(document_id)
                if doc.status not in TERMINAL:
                    self._fail(doc, f"exceeded {self.limits.max_ingestion_steps} ingestion steps")
            doc = self._require(document_id)
            self.metrics.put("IngestionCompleted", 1, Status=doc.status.value)
            self.metrics.put("IngestionLatency", (time.perf_counter() - started) * 1000, "Milliseconds")
            self.metrics.put("IngestionTokens", run.budget.tokens)
            return Outcome(doc.document_id, doc.status, doc.error)

    def mark_retries_exhausted(self, document_id: str, reason: str) -> None:
        doc = self.repo.get(document_id)
        if doc and doc.status not in TERMINAL and doc.status is not DocStatus.PENDING_UPLOAD:
            self._fail(doc, f"retries exhausted: {reason}")

    # --- shared steps (also used by reindexing) ---------------------------------------------
    def _artifact(self, doc: Document, name: str) -> str:
        return derived_prefix(doc.tenant_id, doc.document_id, doc.version) + name

    def load_raw(self, doc: Document) -> bytes:
        try:
            return with_retries(
                lambda: self.objects.get(doc.object_key), op="s3_get", max_retries=self.limits.max_retries
            )
        except KeyError as exc:
            raise PermanentError("uploaded object not found") from exc

    def parse_document(self, doc: Document) -> ParsedDocument:
        return parse(doc.filename, self.load_raw(doc), doc.object_key, self.limits, self.ocr)

    def make_chunks(
        self,
        doc: Document,
        parsed: ParsedDocument,
        run: AgentRun,
        previous: ChunkPlan | None,
        note: str | None,
    ) -> tuple[list[Chunk], ChunkPlan, str | None]:
        enrichment = self.chunking_agent.decide(parsed, run, previous, note)
        title = enrichment.title or doc.filename
        chunks = build_chunks(
            doc, parsed.text, enrichment.plan, title, enrichment.keywords, self.limits.max_chunks_per_document
        )
        self.metrics.put("ChunksCreated", len(chunks), Strategy=enrichment.plan.strategy.value)
        return chunks, enrichment.plan, title

    def embed_chunks(self, doc: Document, chunks: list[Chunk]) -> list[Chunk]:
        """Embeds chunks, reusing vectors cached in S3 for unchanged text (cheap re-chunk/retry)."""
        cache_key = self._artifact(doc, f"embeddings-{_safe(self.embedder.model_id)}.json")
        cache: dict[str, list[float]] = {}
        if (self.objects.size(cache_key) or 0) > 0:
            cache = json.loads(self.objects.get(cache_key))
        digests = [hashlib.sha256(c.text.encode()).hexdigest() for c in chunks]
        missing = [i for i, d in enumerate(digests) if d not in cache]
        for start in range(0, len(missing), self.limits.embedding_batch_size):
            batch = missing[start : start + self.limits.embedding_batch_size]
            vectors = with_retries(
                partial(self.embedder.embed, [chunks[i].text for i in batch]),
                op="embed",
                max_retries=self.limits.max_retries,
                base_delay=self.limits.retry_base_delay_s,
            )
            if len(vectors) != len(batch):
                raise TransientError("embedding batch returned the wrong number of vectors")
            for i, vector in zip(batch, vectors, strict=True):
                cache[digests[i]] = vector
        if missing:
            self.objects.put(cache_key, json.dumps(cache).encode(), "application/json")
        self.metrics.put("EmbeddingsComputed", len(missing))
        self.metrics.put("EmbeddingsCached", len(chunks) - len(missing))
        return [
            c.model_copy(update={"embedding": cache[d], "embedding_model": self.embedder.model_id})
            for c, d in zip(chunks, digests, strict=True)
        ]

    def ensure_live_index(self) -> str:
        live = self.index.live_index()
        if live:
            return live
        first = f"{self.settings.index_prefix}1"
        self.index.create_index(first)
        self.index.point_alias(first)
        self.repo.upsert_index_version(
            first,
            "LIVE",
            chunker_version=CHUNKER_VERSION,
            embedding_model=self.embedder.model_id,
            agent_policy_version=AGENT_POLICY_VERSION,
        )
        return first

    # --- state handlers ---------------------------------------------------------------------
    def _on_received(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        data = self.load_raw(doc)
        content_hash = hashlib.sha256(data).hexdigest()
        duplicate = self.repo.find_indexed_duplicate(doc.tenant_id, content_hash, doc.document_id)
        if duplicate is not None and set(duplicate.acl_principals()) == set(doc.acl_principals()):
            # Same bytes and same audience: re-indexing would only duplicate search results.
            # A copy shared with a different audience is indexed, so ACLs stay correct.
            return DocStatus.DUPLICATE, {
                "content_hash": content_hash,
                "size_bytes": len(data),
                "duplicate_of": duplicate.document_id,
                "_detail": f"duplicate of {duplicate.document_id}",
            }
        return DocStatus.PARSING, {"content_hash": content_hash, "size_bytes": len(data), "error": None}

    def _on_parsing(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        parsed = self.parse_document(doc)
        payload = {
            "text": parsed.text,
            "title": parsed.title,
            "kind": parsed.kind.value,
            "pages": parsed.pages,
            "ocr_used": parsed.ocr_used,
        }
        self.objects.put(self._artifact(doc, "parsed.json"), json.dumps(payload).encode(), "application/json")
        if parsed.ocr_used:
            self.metrics.put("OcrDocuments", 1)
        return DocStatus.CHUNKING, {"title": parsed.title}

    def _load_parsed(self, doc: Document) -> ParsedDocument:
        raw = json.loads(self.objects.get(self._artifact(doc, "parsed.json")))
        return ParsedDocument(raw["text"], raw["title"], FileKind(raw["kind"]), raw["pages"], raw["ocr_used"])

    def _on_chunking(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        parsed = self._load_parsed(doc)
        previous: ChunkPlan | None = None
        note: str | None = None
        plan_key = self._artifact(doc, "plan.json")
        if doc.rechunk_attempts > 0 and (self.objects.size(plan_key) or 0) > 0:
            raw = json.loads(self.objects.get(plan_key))
            previous = ChunkPlan(Strategy(raw["strategy"]), raw["target_chars"], raw["overlap_chars"])
            note = raw.get("verifier_note")
        chunks, plan, title = self.make_chunks(doc, parsed, run, previous, note)
        self.objects.put(
            plan_key,
            json.dumps(
                {
                    "strategy": plan.strategy.value,
                    "target_chars": plan.target_chars,
                    "overlap_chars": plan.overlap_chars,
                }
            ).encode(),
            "application/json",
        )
        self._write_chunks(doc, chunks)
        return DocStatus.EMBEDDING, {
            "chunk_count": len(chunks),
            "title": title,
            "chunker_version": CHUNKER_VERSION,
            "_detail": f"strategy={plan.strategy.value} target={plan.target_chars}",
        }

    def _write_chunks(self, doc: Document, chunks: list[Chunk]) -> None:
        body = json.dumps([c.model_dump() for c in chunks]).encode()
        self.objects.put(self._artifact(doc, f"chunks-{CHUNKER_VERSION}.json"), body, "application/json")

    def load_chunks(self, doc: Document) -> list[Chunk]:
        raw = json.loads(self.objects.get(self._artifact(doc, f"chunks-{CHUNKER_VERSION}.json")))
        return [Chunk.model_validate(c) for c in raw]

    def _on_embedding(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        chunks = self.embed_chunks(doc, self.load_chunks(doc))
        self._write_chunks(doc, chunks)
        return DocStatus.INDEXING, {"embedding_model": self.embedder.model_id}

    def _on_indexing(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        live = self.ensure_live_index()
        chunks = self.load_chunks(doc)
        if any(c.embedding is None for c in chunks):
            raise PermanentError("chunks reached indexing without embeddings")
        with_retries(
            lambda: self.index.upsert(chunks), op="index_upsert", max_retries=self.limits.max_retries
        )
        stale = self.index.delete_stale(doc.document_id, {c.chunk_id for c in chunks})
        if stale:
            self.metrics.put("StaleChunksDeleted", stale)
        if any(c.injection_suspect for c in chunks):
            self.metrics.put("InjectionSuspectChunks", sum(c.injection_suspect for c in chunks))
            log_event(
                logger,
                "injection_suspect_chunks",
                logging.WARNING,
                count=sum(c.injection_suspect for c in chunks),
            )
        return DocStatus.VERIFYING, {
            "index_version": live,
            "_detail": f"indexed {len(chunks)} chunks, {stale} stale removed",
        }

    def _on_verifying(self, doc: Document, run: AgentRun) -> tuple[DocStatus, dict[str, object]]:
        chunks = self.load_chunks(doc)
        expected = {c.chunk_id for c in chunks}
        actual = self.index.chunk_ids(doc.document_id)
        if actual != expected:
            # Usually index refresh lag; retrying the message is the right response.
            raise TransientError(f"index holds {len(actual)} chunks, expected {len(expected)}")

        sample = sample_chunks(chunks, self.limits.verifier_probe_count)
        probes = self.verifier_agent.write_probes(sample, run)
        acl = AccessFilter(tenant_id=doc.tenant_id, principals=tuple(doc.acl_principals()))
        by_id = {c.chunk_id: c for c in chunks}
        failed: list[tuple[Probe, Chunk]] = []
        # A probe passes when its chunk ranks in the top third of its own document (at most
        # top-5), so small documents cannot pass trivially by returning every chunk.
        top_k = max(1, min(PROBE_TOP_K, len(chunks) // 3))
        for probe in probes:
            hits = self.retriever.search([probe.query], acl, k=top_k * 2, document_id=doc.document_id)
            if probe.chunk_id not in [h.chunk.chunk_id for h in hits[:top_k]]:
                failed.append((probe, by_id[probe.chunk_id]))
        pass_rate = 1.0 - len(failed) / max(1, len(probes))
        self.metrics.put("ProbePassRate", pass_rate * 100, "Percent")
        if pass_rate >= self.limits.verifier_min_pass_rate:
            return DocStatus.INDEXED, {"probe_pass_rate": pass_rate, "error": None}

        can_rechunk = doc.rechunk_attempts < self.limits.max_rechunk_attempts
        rec = self.verifier_agent.recommend(failed, pass_rate, can_rechunk, run)
        if rec.action == "rechunk" and can_rechunk:
            plan_key = self._artifact(doc, "plan.json")
            plan = json.loads(self.objects.get(plan_key))
            plan["verifier_note"] = rec.reason
            self.objects.put(plan_key, json.dumps(plan).encode(), "application/json")
            return DocStatus.CHUNKING, {
                "probe_pass_rate": pass_rate,
                "rechunk_attempts": doc.rechunk_attempts + 1,
                "_detail": f"re-chunk: {rec.reason}",
            }
        return DocStatus.NEEDS_REVIEW, {
            "probe_pass_rate": pass_rate,
            "error": f"verification failed: {rec.reason}",
        }

    # --- helpers ----------------------------------------------------------------------------
    def _require(self, document_id: str) -> Document:
        doc = self.repo.get(document_id)
        if doc is None:
            raise PermanentError(f"document {document_id} not found")
        return doc

    def _fail(self, doc: Document, reason: str) -> None:
        self.repo.transition(doc.document_id, doc.status, DocStatus.FAILED, reason, error=reason[:1000])
        self.metrics.put("IngestionFailures", 1, Stage=doc.status.value)
        log_event(logger, "ingestion_failed", logging.ERROR, stage=doc.status.value, reason=reason)


def _safe(model_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in model_id)
