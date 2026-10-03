"""Helpers shared by every eval: dataset loading and per-metric summaries."""

import json


def load_goldens(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize_by_metric(result):
    """Turn a DeepEval result into {metric_name: {n, pass_rate, avg/min/max score}}.

    Metrics are kept separate on purpose. Pooling them would let a drop in one
    metric hide behind the others, and the regression check compares per metric.
    Pass/fail comes from each metric's own `success` flag, so it stays correct
    for metrics where lower is better (toxicity).
    """
    test_results = getattr(result, "test_results", None)
    if test_results is None:
        test_results = result if isinstance(result, list) else []

    buckets = {}
    for test in test_results:
        # field name differs between DeepEval versions
        metrics = getattr(test, "metrics_data", None) or getattr(test, "metrics", None) or []
        for m in metrics:
            name = getattr(m, "name", "unknown")
            b = buckets.setdefault(name, {"scores": [], "passed": 0, "total": 0})
            b["total"] += 1
            if getattr(m, "score", None) is not None:
                b["scores"].append(m.score)
            if getattr(m, "success", False):
                b["passed"] += 1

    summary = {}
    for name, b in buckets.items():
        scores = b["scores"]
        summary[name] = {
            "n": b["total"],
            "pass_rate": 100 * b["passed"] / b["total"] if b["total"] else 0.0,
            "avg_score": sum(scores) / len(scores) if scores else float("nan"),
            "min_score": min(scores) if scores else float("nan"),
            "max_score": max(scores) if scores else float("nan"),
        }
    return summary


def print_summary(title, summary):
    print(f"\n{title}: per-metric summary")
    print("-" * 60)
    for name, s in summary.items():
        avg = "nan" if s["avg_score"] != s["avg_score"] else f"{s['avg_score']:.2f}"
        print(f"  {name:<28} pass_rate={s['pass_rate']:5.0f}%  avg={avg}  n={s['n']}")
    print("-" * 60)
