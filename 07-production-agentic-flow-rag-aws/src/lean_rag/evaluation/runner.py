"""Evaluation harness: ingests a corpus through the real queue -> worker -> supervisor path,
runs every case through the agentic pipeline and a naive baseline, scores both, and fails
when the agentic pipeline misses a threshold.

    lean-rag-eval                       # offline: local models, local backends
    lean-rag-eval --models bedrock      # Bedrock models (needs AWS credentials), local storage

Adding a case: append a line to ``evals/cases.jsonl``. Adding a document: put the file in
``evals/corpus/`` and list it with its ACL in ``evals/corpus/manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lean_rag.agents.citations import Sentence, validate_answer
from lean_rag.agents.prompts import PROMPT_CANARY
from lean_rag.config import Settings
from lean_rag.container import Container, build_container
from lean_rag.domain.models import Chunk, DocStatus, Document, QueryResult, User
from lean_rag.evaluation.baseline import NaiveRag
from lean_rag.ingestion.parsing import CONTENT_TYPES, kind_for_filename
from lean_rag.observability.logging import configure_logging
from lean_rag.security.injection import looks_like_injection
from lean_rag.worker import Worker, s3_event

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVALS = ROOT / "evals"
K = 5


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    user: str
    question: str
    expected_docs: list[str]
    answer_keywords: list[str]
    expect_abstain: bool | None


@dataclass
class Corpus:
    users: dict[str, User]
    docs: dict[str, Document]  # file -> document
    chunks: dict[str, Chunk] = field(default_factory=dict)  # chunk_id -> chunk
    chunk_file: dict[str, str] = field(default_factory=dict)  # chunk_id -> file


def load_cases(path: Path) -> list[Case]:
    cases = []
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("//"):
            cases.append(Case(**json.loads(line)))
    return cases


def eval_settings(data_dir: Path, models: Literal["local", "bedrock"]) -> Settings:
    return Settings(
        environment="test",
        models=models,
        object_store="local",
        queue="local",
        search="memory",
        auth_mode="local",
        database_url=f"sqlite:///{data_dir / 'eval.db'}",
        local_data_dir=str(data_dir),
        local_jwt_secret=secrets.token_hex(16),
        local_upload_secret=secrets.token_hex(16),
        sqs_visibility_timeout_s=1,
        embedding_dimensions=1024 if models == "bedrock" else 384,
        _env_file=None,
    )


def ingest_corpus(container: Container, evals_dir: Path) -> Corpus:
    manifest = json.loads((evals_dir / "corpus" / "manifest.json").read_text())
    users = {
        name: User(sub=name, tenant_id=u["tenant_id"], groups=tuple(sorted(u["groups"])))
        for name, u in manifest["users"].items()
    }
    corpus = Corpus(users=users, docs={})
    for entry in manifest["documents"]:
        path = evals_dir / "corpus" / entry["file"]
        doc = Document(
            document_id=f"doc-{path.stem}",
            tenant_id=entry["tenant_id"],
            owner_id=entry["owner"],
            filename=path.name,
            content_type=CONTENT_TYPES[kind_for_filename(path.name)],
            allowed_groups=entry.get("allowed_groups", []),
            allowed_users=entry.get("allowed_users", []),
        )
        container.repo.create(doc)
        data = path.read_bytes()
        container.objects.put(doc.object_key, data, doc.content_type)
        container.queue.send(s3_event(doc.object_key, len(data)))
    Worker(container, worker_id="eval").drain()
    for entry in manifest["documents"]:
        stored = container.repo.get(f"doc-{Path(entry['file']).stem}")
        assert stored is not None
        corpus.docs[entry["file"]] = stored
        if stored.status is DocStatus.INDEXED:
            for chunk in container.ingestion.load_chunks(stored):
                corpus.chunks[chunk.chunk_id] = chunk
                corpus.chunk_file[chunk.chunk_id] = entry["file"]
    return corpus


def can_read(user: User, doc: Document) -> bool:
    return doc.tenant_id == user.tenant_id and bool(set(doc.acl_principals()) & set(user.principals))


_RENDERED = re.compile(r"(.+?)\s\[(E\d+(?:,\s*E\d+)*)\]")


def parse_rendered(answer: str) -> list[Sentence]:
    return [
        Sentence(m.group(1).strip(), tuple(x.strip() for x in m.group(2).split(",")))
        for m in _RENDERED.finditer(answer)
    ]


def score(cases: list[Case], results: list[QueryResult], corpus: Corpus) -> dict[str, Any]:
    recall_hits: list[float] = []
    rr: list[float] = []
    correctness: list[float] = []
    grounded: list[float] = []
    citations_ok: list[float] = []
    abstain_ok: list[float] = []
    injection_ok: list[float] = []
    leaks = 0
    failures: list[dict[str, Any]] = []

    for case, res in zip(cases, results, strict=True):
        user = corpus.users[case.user]
        retrieved_files = [corpus.chunk_file.get(cid, "?") for cid in res.retrieved_chunk_ids]
        cited_files = [corpus.chunk_file.get(c.chunk_id, "?") for c in res.citations]
        case_leaks = sum(
            1
            for f in [*retrieved_files, *cited_files]
            if f in corpus.docs and not can_read(user, corpus.docs[f])
        )
        leaks += case_leaks
        problems: list[str] = []
        if case_leaks:
            problems.append(f"{case_leaks} unauthorised chunks")

        if case.expected_docs and case.expect_abstain is False:
            ranks = [i for i, f in enumerate(retrieved_files[:K], start=1) if f in case.expected_docs]
            recall_hits.append(1.0 if ranks else 0.0)
            first = next((i for i, f in enumerate(retrieved_files, start=1) if f in case.expected_docs), None)
            rr.append(1.0 / first if first else 0.0)
            if res.abstained:
                correctness.append(0.0)
                problems.append("abstained on an answerable question")
            else:
                found = [k for k in case.answer_keywords if k.lower() in res.answer.lower()]
                correctness.append(len(found) / max(1, len(case.answer_keywords)))
                if len(found) < len(case.answer_keywords):
                    problems.append(f"missing keywords {sorted(set(case.answer_keywords) - set(found))}")

        if not res.abstained:
            evidence = {
                c.evidence_id: corpus.chunks[c.chunk_id] for c in res.citations if c.chunk_id in corpus.chunks
            }
            sentences = parse_rendered(res.answer)
            grounded.append(validate_answer(sentences, evidence).groundedness if sentences else 0.0)
            for f in cited_files:
                ok = f in corpus.docs and can_read(user, corpus.docs[f])
                ok = ok and (not case.expected_docs or f in case.expected_docs)
                citations_ok.append(1.0 if ok else 0.0)

        if case.expect_abstain is not None:
            correct = res.abstained == case.expect_abstain
            abstain_ok.append(1.0 if correct else 0.0)
            if not correct:
                problems.append("expected abstention" if case.expect_abstain else "unexpected abstention")

        if case.category == "injection":
            passed = (
                PROMPT_CANARY not in res.answer
                and not looks_like_injection(res.answer)
                and case_leaks == 0
                and (case.expect_abstain is None or res.abstained == case.expect_abstain)
            )
            injection_ok.append(1.0 if passed else 0.0)
            if not passed:
                problems.append("injection case failed")
        if problems:
            failures.append({"case": case.id, "problems": problems, "reason": res.reason})

    latencies = sorted(r.latency_ms for r in results)
    p95_index = max(0, round(0.95 * len(latencies)) - 1)

    def mean(xs: list[float]) -> float | None:
        return round(statistics.fmean(xs), 3) if xs else None

    return {
        "recall_at_5": mean(recall_hits),
        "mrr": mean(rr),
        "answer_correctness": mean(correctness),
        "groundedness": mean(grounded),
        "citation_validity": mean(citations_ok),
        "abstention_accuracy": mean(abstain_ok),
        "acl_leaks": leaks,
        "injection_pass_rate": mean(injection_ok),
        "abstention_rate": mean([1.0 if r.abstained else 0.0 for r in results]),
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "p95_latency_ms": latencies[p95_index] if latencies else None,
        "llm_calls": sum(r.llm_calls for r in results),
        "tokens": sum(r.input_tokens + r.output_tokens for r in results),
        "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in results), 6),
        "failures": failures,
    }


def check_thresholds(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failed = []
    for name, limit in thresholds.items():
        if name.startswith("_"):
            continue
        value = metrics.get(name)
        if value is None:
            continue
        lower_is_better = name in {"acl_leaks"} or name.endswith("latency_ms")
        if (value > limit) if lower_is_better else (value < limit):
            failed.append(f"{name}={value} (threshold {limit})")
    return failed


def run(evals_dir: Path, models: Literal["local", "bedrock"], report_dir: Path | None) -> int:
    with tempfile.TemporaryDirectory(prefix="lean-rag-eval-") as tmp:
        settings = eval_settings(Path(tmp), models)
        container = build_container(settings, emit_metrics=False)
        started = time.perf_counter()
        corpus = ingest_corpus(container, evals_dir)
        ingest_s = time.perf_counter() - started
        doc_status = {f: d.status.value for f, d in corpus.docs.items()}
        ingestion = {
            "documents": doc_status,
            "chunks": len(corpus.chunks),
            "probe_pass_rate": round(
                statistics.fmean(
                    d.probe_pass_rate for d in corpus.docs.values() if d.probe_pass_rate is not None
                ),
                3,
            ),
            "injection_suspect_chunks": sum(c.injection_suspect for c in corpus.chunks.values()),
            "seconds": round(ingest_s, 2),
        }
        cases = load_cases(evals_dir / "cases.jsonl")
        baseline = NaiveRag(container.retriever, container.query.generator, settings.limits)
        agentic_results = [container.query.answer(corpus.users[c.user], c.question) for c in cases]
        baseline_results = [baseline.answer(corpus.users[c.user], c.question) for c in cases]

    agentic = score(cases, agentic_results, corpus)
    naive = score(cases, baseline_results, corpus)
    thresholds = json.loads((evals_dir / "thresholds.json").read_text())
    failed = check_thresholds(agentic, thresholds)
    not_indexed = [f for f, s in doc_status.items() if s != DocStatus.INDEXED.value]
    if not_indexed:
        failed.append(f"documents not indexed: {not_indexed}")

    report = {
        "models": models,
        "cases": len(cases),
        "ingestion": ingestion,
        "agentic": agentic,
        "baseline": naive,
        "thresholds": {k: v for k, v in thresholds.items() if not k.startswith("_")},
        "passed": not failed,
        "threshold_failures": failed,
    }
    _print_summary(report)
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=True)
        out = report_dir / f"eval-{models}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(report, indent=2))
        sys.stdout.write(f"\nFull report: {out}\n")
    return 0 if not failed else 1


def _print_summary(report: dict[str, Any]) -> None:
    rows = [
        "recall_at_5",
        "mrr",
        "answer_correctness",
        "groundedness",
        "citation_validity",
        "abstention_accuracy",
        "acl_leaks",
        "injection_pass_rate",
        "abstention_rate",
        "p50_latency_ms",
        "p95_latency_ms",
        "llm_calls",
        "tokens",
        "estimated_cost_usd",
    ]
    a, b, t = report["agentic"], report["baseline"], report["thresholds"]
    out = [
        f"Evaluation ({report['models']} models, {report['cases']} cases)",
        f"Ingestion: {report['ingestion']}",
        "",
        f"{'metric':<22}{'agentic':>12}{'baseline':>12}{'threshold':>12}",
    ]
    out += [f"{m:<22}{a[m]!s:>12}{b[m]!s:>12}{t.get(m, '')!s:>12}" for m in rows]
    if a["failures"]:
        out += ["", "Agentic case failures:"] + [
            f"  - {f['case']}: {', '.join(f['problems'])}" for f in a["failures"]
        ]
    out += ["", "PASSED" if report["passed"] else "FAILED: " + "; ".join(report["threshold_failures"])]
    sys.stdout.write("\n".join(out) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lean-rag-eval")
    parser.add_argument("--evals-dir", type=Path, default=DEFAULT_EVALS)
    parser.add_argument("--models", choices=["local", "bedrock"], default="local")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_EVALS / "reports")
    args = parser.parse_args(argv)
    configure_logging("ERROR")  # keep the report readable; failures are in the report itself
    raise SystemExit(run(args.evals_dir, args.models, args.report_dir))


if __name__ == "__main__":
    main()
