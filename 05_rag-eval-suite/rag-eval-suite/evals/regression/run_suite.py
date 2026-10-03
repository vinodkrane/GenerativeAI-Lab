"""Run the whole eval suite against one pipeline and write a snapshot.

The pipeline is built once and passed to every eval, so a snapshot measures a
single pipeline. A baseline and a candidate then differ only by the change under
test.

    python -m evals.regression.run_suite --baseline
    python -m evals.regression.run_suite --label "fetch_k=20"
    python -m evals.regression.run_suite --out path.json --full --quiet
"""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.pipeline import RagPipeline
from app.retriever import RerankingRetriever

from evals import operational, safety
from evals.quality import answer_quality, generation, rag_triad, retrieval
from evals.regression.rules import rule_for

load_dotenv()


# CONFIG
BASELINE_PATH  = "baselines/baseline.json"
CANDIDATE_PATH = "baselines/candidate.json"


# FLATTENING  (every eval -> dotted metric ids)
# Quality evals return NESTED {metric_name: {stat: val}} (from summarize_by_metric).
# ops/safety return ALREADY-FLAT {"sub.stat": val}. We normalize both to a single
# 3-level id space: "<namespace>.<source>.<stat>", e.g.
#   retriever.contextual_recall.pass_rate
#   safety.scope.pass_rate
#   ops.latency.e2e_p95_ms
# These strings are the metric ids the registry + compare.py will key on.
def _slug(name):
    return name.strip().lower().replace(" ", "_")


def flatten_nested(namespace, summary):
    """{metric: {stat: val}} -> {'namespace.metric_slug.stat': val}."""
    out = {}
    for metric, stats in summary.items():
        slug = _slug(metric)
        for stat, val in stats.items():
            out[f"{namespace}.{slug}.{stat}"] = val
    return out


def prefix_flat(namespace, flat):
    """{'sub.stat': val} -> {'namespace.sub.stat': val}."""
    return {f"{namespace}.{key}": val for key, val in flat.items()}


# RUN METADATA  (provenance -- what produced these numbers)
def _git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _prompt_hash():
    # Hash the generator prompt so a silent prompt edit shows up as a new hash.
    try:
        from app.generator import prompt
        return hashlib.sha256(str(prompt).encode()).hexdigest()[:12]
    except Exception:
        return "unknown"


def build_metadata(label):
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "prompt_hash": _prompt_hash(),
        "label": label,          # human note: what change this snapshot represents
            }


# THE SUITE
def run_suite(label="", quiet=False, full=False):
    """Build one pipeline, run every eval against it, return a snapshot dict.

    By default the snapshot keeps only what drives a decision -- gates and
    guardrails (pass rates + headline operational numbers). The distribution
    stats (avg/min/max/n) and secondary operational numbers are 'info' in the
    registry; they're written only with full=True. The registry is the single
    source of truth: the same rules that decide the verdict decide what we
    persist.
    """
    verbose = not quiet
    t_start = time.perf_counter()

    # --- build ONCE, inject everywhere ---
    print("Building pipeline (once)...")
    rag = RagPipeline()
    # The retriever eval should measure the SAME retriever the pipeline uses, not
    # a fresh copy. Fall back to constructing one only if the pipeline doesn't
    # expose it.
    retriever = getattr(rag, "retriever", None) or RerankingRetriever()

    metrics = {}

    # --- QUALITY (component -> pipeline -> application) ---
    print("\n[1/6] retriever eval...")
    metrics.update(flatten_nested("retriever", retrieval.run(retriever)))

    print("\n[2/6] generator eval...")            # no pipeline: tests generate() on golden context
    metrics.update(flatten_nested("generator", generation.run()))

    print("\n[3/6] pipeline (triad) eval...")
    metrics.update(flatten_nested("pipeline", rag_triad.run(rag)))

    print("\n[4/6] application quality eval...")
    metrics.update(flatten_nested("application", answer_quality.run(rag)))

    # --- SAFETY (hard gates) ---
    print("\n[5/6] safety evals...")
    metrics.update(prefix_flat("safety", safety.run_safety(rag, verbose=verbose)))

    # --- OPERATIONAL ---
    print("\n[6/6] operational evals...")
    metrics.update(prefix_flat("ops", operational.run_ops(rag, verbose=verbose)))

    elapsed = time.perf_counter() - t_start

    # Trim to what matters unless --full: drop 'info' metrics (avg/min/max/n,
    # secondary latency/cost) and keep gates + guardrails. ~88 metrics -> ~22.
    if not full:
        metrics = {mid: v for mid, v in metrics.items()
                   if rule_for(mid)["kind"] != "info"}

    snapshot = {
        "metadata": {
            **build_metadata(label),
            "suite_seconds": round(elapsed, 1),
            "full": full,
            "n_metrics": len(metrics),
        },
        "metrics": metrics,
    }
    return snapshot


# WRITE + REPORT
def write_snapshot(snapshot, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
    return path


def print_snapshot(snapshot):
    meta = snapshot["metadata"]
    metrics = snapshot["metrics"]
    print("\n" + "=" * 74)
    print("SNAPSHOT")
    print("=" * 74)
    print(f"label       : {meta.get('label') or '(none)'}")
    print(f"created_at  : {meta['created_at']}")
    print(f"git_sha     : {meta['git_sha']}    prompt_hash: {meta['prompt_hash']}")
    print(f"suite_time  : {meta['suite_seconds']}s     metrics: {len(metrics)}")
    print("-" * 74)
    for key in sorted(metrics):
        val = metrics[key]
        shown = f"{val:.4f}" if isinstance(val, float) else str(val)
        print(f"  {key:<44} {shown}")
    print("=" * 74)


# ENTRYPOINT
def main():
    parser = argparse.ArgumentParser(description="Run the full eval suite and write a snapshot.")
    parser.add_argument("--baseline", action="store_true",
                        help=f"write to {BASELINE_PATH} (bless this run as the baseline)")
    parser.add_argument("--out", default=None, help="custom output path")
    parser.add_argument("--label", default="", help="describe the change this snapshot represents")
    parser.add_argument("--quiet", action="store_true", help="suppress per-eval ops/safety chatter")
    parser.add_argument("--full", action="store_true",
                        help="also write info metrics (avg/min/max/n, secondary latency/cost)")
    args = parser.parse_args()

    out = args.out or (BASELINE_PATH if args.baseline else CANDIDATE_PATH)

    snapshot = run_suite(label=args.label, quiet=args.quiet, full=args.full)
    print_snapshot(snapshot)
    path = write_snapshot(snapshot, out)
    print(f"\nwrote snapshot -> {path}")


if __name__ == "__main__":
    main()