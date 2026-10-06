"""Versioned reindexing: build ``chunks_vN`` from S3, evaluate it, then switch the live alias.

The live index is never modified by a rebuild. Promotion is an atomic alias swap, and the
previous index is kept (status RETIRED) so ``rollback`` is another alias swap.

    lean-rag-reindex status
    lean-rag-reindex build --version 2      # build + evaluate -> READY or REJECTED
    lean-rag-reindex promote --index chunks_v2
    lean-rag-reindex rollback
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from lean_rag.agents.base import AgentRun
from lean_rag.agents.verifier_agent import heuristic_probe, sample_chunks
from lean_rag.config import AGENT_POLICY_VERSION, CHUNKER_VERSION, get_settings
from lean_rag.container import Container, build_container
from lean_rag.domain.models import Chunk, DocStatus, Document, utcnow
from lean_rag.observability.logging import configure_logging, log_event
from lean_rag.reliability import Budget, PermanentError, with_retries
from lean_rag.security.acl import AccessFilter

logger = logging.getLogger(__name__)
PROBES_PER_DOC = 3
TOP_K = 5
MIN_DOC_RECALL = 0.8
MAX_REGRESSION = 0.05


@dataclass
class ProbeSample:
    acl: AccessFilter
    chunk: Chunk
    query: str


class Reindexer:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.limits = container.settings.limits

    def _index_doc(self, doc: Document, target: str) -> list[Chunk]:
        ing = self.c.ingestion
        run = AgentRun(
            budget=Budget(self.limits.max_llm_calls_per_ingestion, self.limits.max_tokens_per_ingestion),
            agent_run_id=uuid.uuid4().hex,
        )
        parsed = ing.parse_document(doc)
        chunks, _, _ = ing.make_chunks(doc, parsed, run, None, None)
        chunks = ing.embed_chunks(doc, chunks)
        with_retries(
            lambda: self.c.index.upsert(chunks, index=target),
            op="reindex_upsert",
            max_retries=self.limits.max_retries,
        )
        self.c.index.delete_stale(doc.document_id, {c.chunk_id for c in chunks}, index=target)
        return chunks

    def build(self, version: int) -> dict[str, object]:
        target = f"{self.c.settings.index_prefix}{version}"
        live = self.c.index.live_index()
        if target == live:
            raise SystemExit(f"{target} is live; refusing to rebuild it in place")
        if self.c.index.index_exists(target):
            raise SystemExit(f"{target} already exists; choose a new version number")
        self.c.index.create_index(target)
        self.c.repo.upsert_index_version(
            target,
            "BUILDING",
            chunker_version=CHUNKER_VERSION,
            embedding_model=self.c.embedder.model_id,
            agent_policy_version=AGENT_POLICY_VERSION,
        )
        samples: list[ProbeSample] = []
        failures: list[str] = []
        started: datetime = utcnow()
        done: set[tuple[str, int]] = set()
        # Second pass catches documents indexed into the live alias while the build ran.
        for updated_after in (None, started):
            for doc in self.c.repo.list_by_status([DocStatus.INDEXED], updated_after=updated_after):
                if (doc.document_id, doc.version) in done:
                    continue
                try:
                    chunks = self._index_doc(doc, target)
                except PermanentError as exc:
                    failures.append(f"{doc.document_id}: {exc}")
                    continue
                done.add((doc.document_id, doc.version))
                acl = AccessFilter(doc.tenant_id, tuple(doc.acl_principals()))
                samples.extend(
                    ProbeSample(acl, c, heuristic_probe(c)) for c in sample_chunks(chunks, PROBES_PER_DOC)
                )
        report = self.evaluate(target, live, samples)
        report["documents"] = len(done)
        report["failures"] = failures
        status = "READY" if report["passed"] else "REJECTED"
        self.c.repo.upsert_index_version(target, status, eval_report=report)
        log_event(logger, "reindex_built", index=target, status=status, report=report)
        return {"index": target, "status": status, **report}

    def _recall(self, index: str, samples: list[ProbeSample]) -> tuple[float, float]:
        doc_hits = chunk_hits = 0
        for s in samples:
            hits = self.c.retriever.search([s.query], s.acl, k=TOP_K * 2, index=index)[:TOP_K]
            doc_hits += any(h.chunk.document_id == s.chunk.document_id for h in hits)
            chunk_hits += any(h.chunk.chunk_id == s.chunk.chunk_id for h in hits)
        n = max(1, len(samples))
        return doc_hits / n, chunk_hits / n

    def evaluate(self, target: str, live: str | None, samples: list[ProbeSample]) -> dict[str, object]:
        new_doc, new_chunk = self._recall(target, samples)
        live_doc = self._recall(live, samples)[0] if live else None
        passed = new_doc >= MIN_DOC_RECALL and (live_doc is None or new_doc >= live_doc - MAX_REGRESSION)
        if not samples:
            passed = live is None  # an empty rebuild must never replace a populated index
        return {
            "probes": len(samples),
            "doc_recall_at_5": round(new_doc, 3),
            "chunk_recall_at_5": round(new_chunk, 3),
            "live_doc_recall_at_5": None if live_doc is None else round(live_doc, 3),
            "passed": passed,
        }

    def promote(self, index: str, force: bool = False) -> None:
        versions = {v["index_name"]: v for v in self.c.repo.list_index_versions()}
        record = versions.get(index)
        if record is None:
            raise SystemExit(f"{index} is unknown")
        if record["status"] != "READY" and not force:
            raise SystemExit(f"{index} is {record['status']}; only READY indexes can be promoted")
        previous = self.c.index.live_index()
        self.c.index.point_alias(index)
        self.c.repo.upsert_index_version(index, "LIVE")
        if previous and previous != index:
            self.c.repo.upsert_index_version(previous, "RETIRED")
        log_event(logger, "index_promoted", index=index, previous=previous)

    def rollback(self) -> str:
        retired = [v for v in self.c.repo.list_index_versions() if v["status"] == "RETIRED"]
        if not retired:
            raise SystemExit("no retired index to roll back to")
        target = str(max(retired, key=lambda v: v["updated_at"])["index_name"])
        current = self.c.index.live_index()
        self.c.index.point_alias(target)
        self.c.repo.upsert_index_version(target, "LIVE")
        if current:
            self.c.repo.upsert_index_version(current, "READY")
        log_event(logger, "index_rolled_back", index=target, previous=current)
        return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lean-rag-reindex")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("build")
    b.add_argument("--version", type=int, required=True)
    p = sub.add_parser("promote")
    p.add_argument("--index", required=True)
    p.add_argument("--force", action="store_true")
    sub.add_parser("rollback")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    reindexer = Reindexer(build_container(settings, emit_metrics=False))
    if args.cmd == "status":
        out: object = {
            "live": reindexer.c.index.live_index(),
            "versions": reindexer.c.repo.list_index_versions(),
        }
    elif args.cmd == "build":
        out = reindexer.build(args.version)
    elif args.cmd == "promote":
        reindexer.promote(args.index, args.force)
        out = {"live": args.index}
    else:
        out = {"live": reindexer.rollback()}
    sys.stdout.write(json.dumps(out, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
