"""Operational evals: latency, cost and reliability.

No datasets and no LLM judge here, only measurements.

  latency      percentiles (p95/p99, not the mean) for the full answer and for
               time-to-first-token, split into retrieval and generation. Warmup
               runs are discarded so cold start does not skew the numbers.
  cost         tokens x price, read from the model's usage metadata. Stable at
               temperature 0, so it can be checked before launch.
  reliability  success, error and retry rates with exponential backoff.

run_ops() runs all three and returns a flat dict for the regression suite.

    python -m evals.operational
"""

# IMPORTS & ENV
import math
import time
from dotenv import load_dotenv

from app.pipeline import RagPipeline
from app.generator import generate, generate_stream, prompt, llm

load_dotenv()

# COST reads token usage off the AIMessage, so it composes prompt | llm directly
# and stops BEFORE the StrOutputParser() that generate() uses (which discards
# usage and returns a bare string). Same prompt, same model, real context.
measured_chain = prompt | llm


# SHARED CONFIG
# One representative question set, reused by all three evals. In a real suite
# these should be segmented (simple / medium / complex) so the numbers reflect
# the traffic you actually get, not just easy questions.
QUESTIONS = [
    "What is the difference between reference-based and reference-free evals?",
    "Explain what faithfulness measures in a RAG pipeline.",
    "How does the G-Eval metric assign a score?",
    "What is MMLU and why is contamination a problem?",
]


# SHARED HELPERS
# Linear-interpolation percentile -- same result as numpy.percentile, but
# written out so the tail is not a black box when you explain it in class.
def percentile(values, p):
    values = [v for v in values if not math.isnan(v)]
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def col_avg(rows, key):
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


# --- LATENCY ---
# --- LATENCY: CONFIG ---
LAT_REPEATS = 5           # measured runs per question -> samples = len(QUESTIONS)*LAT_REPEATS
LAT_WARMUP_RUNS = 2       # throwaway calls before measuring (cold start)
LAT_MEASURE_TTFT = True   # stream generation and clock time-to-first-token
LAT_STAGE_LEVEL = True    # split retrieval vs generation (implied when TTFT is on)

SLO_P95_MS = 3000         # end-to-end: full answer p95 under 3s
SLO_TTFT_P95_MS = 1200    # perceived: first visible token p95 under 1.2s


# --- LATENCY: PIPELINE ADAPTERS (the ONE place you edit to match your API) ---
# End-to-end: invoke() returns {"query", "context", "answer"} -- we want answer.
def lat_end_to_end(pipeline, question):
    return pipeline.invoke(question)["answer"]


# Stage-level (non-streaming): time retrieval vs generation. "retrieval" bundles
# query-embedding + vector search + the cross-encoder rerank pass.
def lat_stages(pipeline, question):
    t0 = time.perf_counter()
    docs = pipeline.retriever.invoke(question)
    context = [doc.page_content for doc in docs]
    t1 = time.perf_counter()
    answer = generate(question, context)
    t2 = time.perf_counter()
    return answer, {"retrieval": (t1 - t0) * 1000, "generation": (t2 - t1) * 1000}


# Stage-level (streaming): same retrieval, but stream generation and record the
# clock the instant the FIRST content token arrives.
#   ttft       = query submitted (t0) -> first visible token (what the user feels)
#   generation = generation start (t1) -> last token
# TTFT includes retrieval on purpose: the user waits through retrieval before the
# first token can stream, so perceived latency = retrieval + generation-prefill.
def lat_stages_streaming(pipeline, question):
    t0 = time.perf_counter()
    docs = pipeline.retriever.invoke(question)
    context = [doc.page_content for doc in docs]
    t1 = time.perf_counter()

    first_token_t = None
    pieces = []
    for piece in generate_stream(question, context):
        if first_token_t is None:
            first_token_t = time.perf_counter()   # clock the first non-empty chunk
        pieces.append(piece)
    t2 = time.perf_counter()

    answer = "".join(pieces)
    ttft_ms = (first_token_t - t0) * 1000 if first_token_t else float("nan")
    return answer, {
        "retrieval": (t1 - t0) * 1000,
        "generation": (t2 - t1) * 1000,
        "ttft": ttft_ms,
    }


# --- LATENCY: BENCHMARK + AGGREGATE + REPORT ---
def lat_benchmark(pipeline):
    # Warmup: run and DISCARD, so cold start does not pollute the stats.
    print(f"[latency] warming up ({LAT_WARMUP_RUNS} runs, discarded)...")
    for i in range(LAT_WARMUP_RUNS):
        lat_end_to_end(pipeline, QUESTIONS[i % len(QUESTIONS)])

    total_ms, retrieval_ms, generation_ms, ttft_ms = [], [], [], []
    answer_lengths = []

    print("[latency] measuring...")
    for question in QUESTIONS:
        for _ in range(LAT_REPEATS):
            start = time.perf_counter()
            if LAT_MEASURE_TTFT:
                answer, stage = lat_stages_streaming(pipeline, question)
                retrieval_ms.append(stage["retrieval"])
                generation_ms.append(stage["generation"])
                ttft_ms.append(stage["ttft"])
            elif LAT_STAGE_LEVEL:
                answer, stage = lat_stages(pipeline, question)
                retrieval_ms.append(stage["retrieval"])
                generation_ms.append(stage["generation"])
            else:
                answer = lat_end_to_end(pipeline, question)
            elapsed_ms = (time.perf_counter() - start) * 1000

            total_ms.append(elapsed_ms)
            answer_lengths.append(len(answer or ""))

    return {
        "total": total_ms,
        "retrieval": retrieval_ms,
        "generation": generation_ms,
        "ttft": ttft_ms,
        "answer_len": answer_lengths,
    }


def lat_summarize(samples):
    clean = [s for s in samples if not math.isnan(s)]
    return {
        "n": len(clean),
        "mean": sum(clean) / len(clean),
        "p50": percentile(clean, 50),
        "p95": percentile(clean, 95),
        "p99": percentile(clean, 99),
        "min": min(clean),
        "max": max(clean),
    }


def lat_print_row(label, s):
    print(f"{label:<12} | n={s['n']:<3} "
          f"mean={s['mean']:7.1f}  p50={s['p50']:7.1f}  "
          f"p95={s['p95']:7.1f}  p99={s['p99']:7.1f}  "
          f"min={s['min']:7.1f}  max={s['max']:7.1f}")


def lat_slo_line(label, p95, budget):
    verdict = "PASS" if p95 <= budget else "FAIL"
    print(f"SLO: {label:<22} p95 <= {budget:>5} ms  ->  p95 = {p95:7.0f} ms   [{verdict}]")


def lat_report(results):
    print("\n" + "=" * 78)
    print("LATENCY (milliseconds)")
    print("=" * 78)
    print(f"{'stage':<12} | {'samples':<5} {'mean':>11} {'p50':>11} "
          f"{'p95':>11} {'p99':>11} {'min':>11} {'max':>11}")
    print("-" * 78)

    total = lat_summarize(results["total"])
    lat_print_row("end-to-end", total)
    if results["ttft"]:
        lat_print_row("ttft", lat_summarize(results["ttft"]))   # perceived
    if results["retrieval"]:
        lat_print_row("retrieval", lat_summarize(results["retrieval"]))
        lat_print_row("generation", lat_summarize(results["generation"]))

    avg_len = sum(results["answer_len"]) / len(results["answer_len"])
    print("-" * 78)
    print(f"avg answer length: {avg_len:.0f} chars "
          f"(latency scales with output length -- keep in mind when comparing configs)")

    print("=" * 78)
    lat_slo_line("full answer", total["p95"], SLO_P95_MS)
    if results["ttft"]:
        lat_slo_line("first token (perceived)", lat_summarize(results["ttft"])["p95"], SLO_TTFT_P95_MS)
    print("=" * 78)


def run_latency(pipeline, verbose=True):
    """Measure latency; print the report and return a flat metrics dict."""
    results = lat_benchmark(pipeline)
    if verbose:
        lat_report(results)

    total = lat_summarize(results["total"])
    metrics = {
        "e2e_mean_ms": total["mean"],
        "e2e_p50_ms": total["p50"],
        "e2e_p95_ms": total["p95"],
        "e2e_p99_ms": total["p99"],
        "avg_answer_len": sum(results["answer_len"]) / len(results["answer_len"]),
        "slo_e2e_pass": total["p95"] <= SLO_P95_MS,
    }
    if results["ttft"]:
        ttft = lat_summarize(results["ttft"])
        metrics["ttft_p95_ms"] = ttft["p95"]
        metrics["slo_ttft_pass"] = ttft["p95"] <= SLO_TTFT_P95_MS
    if results["retrieval"]:
        metrics["retrieval_p95_ms"] = lat_summarize(results["retrieval"])["p95"]
        metrics["generation_p95_ms"] = lat_summarize(results["generation"])["p95"]
    return metrics


# --- COST ---
# --- COST: CONFIG ---
COST_REPEATS = 3          # cost is stable, so fewer repeats needed than latency

# Pricing: gpt-4o-mini, USD per 1M tokens (verified Aug 2026). Prices change --
# keep them here as constants, never buried in code, and re-check the provider's
# pricing page before trusting a budget.
PRICE_INPUT_PER_1M        = 0.15    # cache-miss input
PRICE_CACHED_INPUT_PER_1M = 0.075   # cached (repeated prefix) input -- half price
PRICE_OUTPUT_PER_1M       = 0.60    # output (4x input -- long answers dominate)

# Business projection knobs (set these to YOUR reality).
QUERIES_PER_DAY = 2000              # expected doubt-solver traffic
USD_TO_INR      = 88.0              # approximate; set to the current rate

# Budget (the "SLO" for cost): the offline pass/fail line.
COST_BUDGET_PER_QUERY_USD = 0.0015  # e.g. must stay under ~0.13 INR / query


# --- COST: TOKEN MEASUREMENT + COST MATH ---
# Retrieve real context (so input tokens reflect your actual retriever load),
# run one generation, and read the token usage off the message.
def cost_measure_tokens(pipeline, question):
    docs = pipeline.retriever.invoke(question)
    context_text = "\n\n".join(doc.page_content for doc in docs)

    msg = measured_chain.invoke({"question": question, "context": context_text})
    usage = msg.usage_metadata or {}

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    # cached prefix tokens, if the provider reports them
    details = usage.get("input_token_details") or {}
    cached_tokens = details.get("cache_read", 0) or 0

    return {"input": input_tokens, "output": output_tokens, "cached": cached_tokens}


# cost = uncached_input @ full rate + cached_input @ cached rate + output @ output rate
def cost_usd(input_tokens, output_tokens, cached_tokens):
    uncached_input = max(input_tokens - cached_tokens, 0)
    c_in     = uncached_input / 1_000_000 * PRICE_INPUT_PER_1M
    c_cached = cached_tokens  / 1_000_000 * PRICE_CACHED_INPUT_PER_1M
    c_out    = output_tokens  / 1_000_000 * PRICE_OUTPUT_PER_1M
    return {"input": c_in, "cached": c_cached, "output": c_out,
            "total": c_in + c_cached + c_out}


# --- COST: BENCHMARK + REPORT ---
def cost_benchmark(pipeline):
    rows = []
    print("[cost] measuring token usage...")
    for question in QUESTIONS:
        for _ in range(COST_REPEATS):
            tok = cost_measure_tokens(pipeline, question)
            cost = cost_usd(tok["input"], tok["output"], tok["cached"])
            rows.append({**tok, **{f"cost_{k}": v for k, v in cost.items()}})
    return rows


def cost_report(rows):
    n = len(rows)
    avg_in     = col_avg(rows, "input")
    avg_out    = col_avg(rows, "output")
    avg_cached = col_avg(rows, "cached")
    avg_cost   = col_avg(rows, "cost_total")
    min_cost   = min(r["cost_total"] for r in rows)
    max_cost   = max(r["cost_total"] for r in rows)

    avg_cost_in  = col_avg(rows, "cost_input") + col_avg(rows, "cost_cached")
    avg_cost_out = col_avg(rows, "cost_output")
    out_share = 100 * avg_cost_out / avg_cost if avg_cost else 0

    print("\n" + "=" * 70)
    print(f"COST  (gpt-4o-mini @ ${PRICE_INPUT_PER_1M}/${PRICE_OUTPUT_PER_1M} per 1M in/out)")
    print("=" * 70)
    print(f"samples                : {n}")
    print(f"avg input tokens       : {avg_in:8.0f}   ({avg_cached:.0f} cached)")
    print(f"avg output tokens      : {avg_out:8.0f}")
    print("-" * 70)
    print(f"avg cost / query       : ${avg_cost:.6f}   (Rs {avg_cost * USD_TO_INR:.4f})")
    print(f"   min / max           : ${min_cost:.6f} / ${max_cost:.6f}   "
          f"<- tight range = cost is stable, unlike latency")
    print(f"   input vs output     : {100 - out_share:.0f}% input / {out_share:.0f}% output "
          f"(output is 4x the rate -> long answers dominate)")
    print("-" * 70)

    daily   = avg_cost * QUERIES_PER_DAY
    monthly = daily * 30
    print(f"projection @ {QUERIES_PER_DAY}/day :")
    print(f"   per day             : ${daily:8.2f}   (Rs {daily * USD_TO_INR:8.2f})")
    print(f"   per month           : ${monthly:8.2f}   (Rs {monthly * USD_TO_INR:8.2f})")
    print("=" * 70)

    verdict = "PASS" if avg_cost <= COST_BUDGET_PER_QUERY_USD else "FAIL"
    print(f"BUDGET: cost/query <= ${COST_BUDGET_PER_QUERY_USD:.6f}  ->  "
          f"${avg_cost:.6f}   [{verdict}]")
    print("=" * 70)
    print("note: production caching of the (large, fixed) system prompt can push the")
    print("real bill BELOW this estimate -- watch the 'cached' count grow online.")


def run_cost(pipeline, verbose=True):
    """Measure cost; print the report and return a flat metrics dict."""
    rows = cost_benchmark(pipeline)
    if verbose:
        cost_report(rows)

    avg_cost = col_avg(rows, "cost_total")
    avg_cost_out = col_avg(rows, "cost_output")
    out_share = 100 * avg_cost_out / avg_cost if avg_cost else 0.0
    return {
        "cost_per_query_usd": avg_cost,
        "cost_per_query_inr": avg_cost * USD_TO_INR,
        "avg_input_tokens": col_avg(rows, "input"),
        "avg_output_tokens": col_avg(rows, "output"),
        "avg_cached_tokens": col_avg(rows, "cached"),
        "output_cost_share_pct": out_share,
        "monthly_usd": avg_cost * QUERIES_PER_DAY * 30,
        "budget_pass": avg_cost <= COST_BUDGET_PER_QUERY_USD,
    }


# --- RELIABILITY ---
# --- RELIABILITY: CONFIG ---
REL_REPEATS = 5
MAX_RETRIES = 2
BACKOFF_BASE_S = 0.5


# --- RELIABILITY: TRACKER + RETRY WRAPPER ---
class Reliability:
    def __init__(self):
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.retries = 0        # NOTE: counts total retry ATTEMPTS, not requests
                                # that needed >=1 retry. Fine at 0 failures; see
                                # the run_reliability note if you want per-request.


def call_with_retries(fn, reliability):
    reliability.calls += 1
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = fn()
            reliability.successes += 1
            return result
        except Exception as e:
            if attempt < MAX_RETRIES:
                reliability.retries += 1
                time.sleep(BACKOFF_BASE_S * (2 ** attempt))   # exponential backoff
            else:
                reliability.failures += 1
                print(f"[reliability] FAILED after {MAX_RETRIES} retries: {e}")
                return None


# --- RELIABILITY: BENCHMARK + REPORT ---
def rel_benchmark(pipeline):
    reliability = Reliability()
    print("[reliability] measuring...")
    for question in QUESTIONS:
        for _ in range(REL_REPEATS):
            call_with_retries(lambda q=question: pipeline.invoke(q), reliability)
    return reliability


def rel_report(rel):
    calls = rel.calls or 1
    success_rate = 100 * rel.successes / calls
    error_rate   = 100 * rel.failures / calls
    retry_rate   = 100 * rel.retries / calls

    print("\n" + "=" * 60)
    print("RELIABILITY")
    print("=" * 60)
    print(f"total requests : {rel.calls}")
    print(f"successful     : {rel.successes}")
    print(f"failed         : {rel.failures}")
    print("-" * 60)
    print(f"success rate   : {success_rate:.2f}%")
    print(f"error rate     : {error_rate:.2f}%")
    print(f"retry rate     : {retry_rate:.2f}%")
    print("=" * 60)


def run_reliability(pipeline, verbose=True):
    """Measure reliability; print the report and return a flat metrics dict."""
    rel = rel_benchmark(pipeline)
    if verbose:
        rel_report(rel)

    calls = rel.calls or 1
    return {
        "total_requests": rel.calls,
        "success_rate": 100 * rel.successes / calls,
        "error_rate": 100 * rel.failures / calls,
        "retry_rate": 100 * rel.retries / calls,
    }


# ENTRYPOINT  (runs all three, returns the operational snapshot)
# One pipeline, three evals, one merged snapshot. The dotted keys become metric
# ids -- this dict is the operational slice of what regression testing diffs
# against a baseline.
def run_ops(pipeline=None, verbose=True):
    pipeline = pipeline or RagPipeline()

    latency     = run_latency(pipeline, verbose=verbose)
    cost        = run_cost(pipeline, verbose=verbose)
    reliability = run_reliability(pipeline, verbose=verbose)

    snapshot = {}
    snapshot.update({f"latency.{k}": v for k, v in latency.items()})
    snapshot.update({f"cost.{k}": v for k, v in cost.items()})
    snapshot.update({f"reliability.{k}": v for k, v in reliability.items()})
    return snapshot


def main():
    snapshot = run_ops(verbose=True)

    print("\n" + "=" * 70)
    print("OPERATIONAL SNAPSHOT  (feeds regression testing)")
    print("=" * 70)
    for key, value in snapshot.items():
        shown = f"{value:.6f}" if isinstance(value, float) else str(value)
        print(f"  {key:<30} {shown}")
    print("=" * 70)


if __name__ == "__main__":
    main()