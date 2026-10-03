"""Compare a candidate snapshot against the baseline and return a verdict.

    PASS    no gate blocked and no guardrail regressed
    REVIEW  a guardrail regressed beyond tolerance, a human decides
    FAIL    a gate regressed, release blocked

The exit code (PASS=0, FAIL=1, REVIEW=2) lets CI use this as a gate.

    python -m evals.regression.compare
    python -m evals.regression.compare --baseline a.json --candidate b.json --all
"""

import argparse
import json
import math
import sys
from collections import Counter

from evals.regression.rules import rule_for

BASELINE_PATH  = "baselines/baseline.json"
CANDIDATE_PATH = "baselines/candidate.json"

EXIT_CODE = {"PASS": 0, "FAIL": 1, "REVIEW": 2}


def load(path):
    with open(path) as f:
        return json.load(f)


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and not (isinstance(x, float) and math.isnan(x))


# CLASSIFY one metric: baseline vs candidate, under its rule
def classify(mid, bv, cv, rule):
    row = {"id": mid, "baseline": bv, "candidate": cv,
           "kind": rule["kind"], "direction": rule["direction"],
           "delta": None, "status": None}

    # present on only one side -- a structural change worth surfacing
    if bv is None:
        row["status"] = "new"
        return row
    if cv is None:
        row["status"] = "dropped"
        return row

    # info metrics: record the delta but never touch the verdict
    if rule["kind"] == "info":
        if _is_number(bv) and _is_number(cv):
            row["delta"] = cv - bv
        row["status"] = "info"
        return row

    # boolean SLO flags: True -> False is a regression
    if rule.get("bool") or isinstance(bv, bool) or isinstance(cv, bool):
        if bv == cv:
            row["status"] = "flat"
        elif (not bv) and cv:                       # False -> True
            row["status"] = "improved"
        else:                                        # True -> False
            row["status"] = "blocked" if rule["kind"] == "gate" else "regressed"
        return row

    # numeric
    if not (_is_number(bv) and _is_number(cv)):
        row["status"] = "info"
        return row

    delta = cv - bv
    row["delta"] = delta
    higher_better = rule["direction"] == "higher"
    improved = (delta > 0) if higher_better else (delta < 0)
    if improved:
        row["status"] = "improved"                   # improvements are never penalized
        return row

    worse = abs(delta)                               # 0 if exactly equal
    tolerance = max(rule["tol"], rule["rel_tol"] * abs(bv))
    if worse <= tolerance:
        row["status"] = "flat"                       # inside the noise band
    else:
        row["status"] = "blocked" if rule["kind"] == "gate" else "regressed"
    return row


# COMPARE two snapshots -> (verdict, rows)
def compare(baseline, candidate):
    b = baseline.get("metrics", {})
    c = candidate.get("metrics", {})

    rows = [classify(mid, b.get(mid), c.get(mid), rule_for(mid))
            for mid in sorted(set(b) | set(c))]

    if any(r["status"] == "blocked" for r in rows):
        verdict = "FAIL"                             # a gate regressed
    elif any(r["status"] == "regressed" for r in rows):
        verdict = "REVIEW"                           # a guardrail regressed
    else:
        verdict = "PASS"
    return verdict, rows


# REPORT
def _fmt(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:.4g}"
    return str(v)


def print_report(verdict, rows, show_all=False):
    order = {"blocked": 0, "regressed": 1, "dropped": 2, "new": 3,
             "improved": 4, "flat": 5, "info": 6}
    shown = [r for r in rows if show_all or r["kind"] != "info"]
    shown.sort(key=lambda r: (order.get(r["status"], 9), r["id"]))

    print("=" * 96)
    print(f"{'metric':<46} {'baseline':>10} {'candidate':>10} {'delta':>10}  status")
    print("-" * 96)
    for r in shown:
        d = r["delta"]
        dstr = f"{d:+.4g}" if isinstance(d, (int, float)) and not isinstance(d, bool) else ""
        marker = "  <<<" if r["status"] in ("blocked", "regressed") else ""
        print(f"{r['id']:<46} {_fmt(r['baseline']):>10} {_fmt(r['candidate']):>10} "
              f"{dstr:>10}  {r['status']}{marker}")

    counts = Counter(r["status"] for r in rows)
    line = "  ".join(f"{k}={counts[k]}" for k in
                     ["blocked", "regressed", "improved", "flat", "new", "dropped", "info"]
                     if counts[k])
    print("-" * 96)
    print(line)
    info_n = counts["info"]
    if not show_all and info_n:
        print(f"({info_n} info metrics hidden; pass --all to show them)")
    print("=" * 96)

    banner = {
        "PASS":   "PASS   -- no gate blocked, no guardrail regressed. Safe to promote.",
        "REVIEW": "REVIEW -- a guardrail regressed beyond tolerance. A human decides.",
        "FAIL":   "FAIL   -- a gate regressed. Blocked.",
    }[verdict]
    print(f"VERDICT: {banner}")
    print("=" * 96)


# ENTRYPOINT
def main():
    ap = argparse.ArgumentParser(description="Compare two eval snapshots and return a verdict.")
    ap.add_argument("--baseline", default=BASELINE_PATH)
    ap.add_argument("--candidate", default=CANDIDATE_PATH)
    ap.add_argument("--all", action="store_true", help="also show info metrics")
    args = ap.parse_args()

    baseline = load(args.baseline)
    candidate = load(args.candidate)

    bl = baseline.get("metadata", {})
    cl = candidate.get("metadata", {})
    print(f"baseline  : {bl.get('label') or args.baseline}   "
          f"({bl.get('git_sha', '?')}, prompt {bl.get('prompt_hash', '?')})")
    print(f"candidate : {cl.get('label') or args.candidate}   "
          f"({cl.get('git_sha', '?')}, prompt {cl.get('prompt_hash', '?')})")

    verdict, rows = compare(baseline, candidate)
    print_report(verdict, rows, show_all=args.all)
    sys.exit(EXIT_CODE[verdict])


if __name__ == "__main__":
    main()