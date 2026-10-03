# RAG evaluation strategy

How to evaluate a RAG system so that a bad score tells you what to fix. The
structure here is what this repo implements.

## The core idea

A RAG answer is produced by a chain: chunking, embedding, retrieval, reranking,
prompting, generation. If you only score the final answer, a failure could come
from any link. Evaluate each component on its own, then the pipeline, then the
application, then production.

```
                    +-------------------------+
 Production         |  online monitoring      |  sampled live traffic, no labels
                    +-------------------------+
                    +-------------------------+
 Application        |  end-to-end quality     |  correctness, completeness, safety, ops
                    +-------------------------+
                    +-------------------------+
 Pipeline           |  RAG triad              |  retrieval + generation together
                    +-------------------------+
            +---------------+   +---------------+
 Components |   retriever   |   |   generator   |  each tested alone
            +---------------+   +---------------+
```

Work from the bottom up when building, from the top down when debugging.

## 1. Evaluating the retriever

**Goal**: the right evidence is in the top k, and ranked high.

**Test**: run only the retriever over the golden questions. Do not call the
generator. Score the chunks it returns.

**Metrics**: contextual recall (did we miss anything), contextual precision
(is the good stuff ranked first). If you have chunk-level labels, add hit rate,
MRR and nDCG.

**What to vary and compare**

| Knob | Typical effect |
|---|---|
| Chunk size | Small chunks are precise but lose context; large chunks carry noise |
| Overlap | Protects facts that straddle a boundary |
| Embedding model | Largest single effect on recall |
| `fetch_k` | Higher recall, more rerank cost |
| Reranker | Large precision gain for modest latency |
| Hybrid search | Helps exact terms, names, IDs that embeddings blur |
| Query rewriting | Helps vague or multi-part questions |

**Diagnose with**: pick a failing question, print the retrieved chunks, and ask
whether the answer was in the corpus at all. If not, that is a data problem. If
it was, but ranked 8th, that is a ranking problem. They have different fixes.

**Repo**: `evals/quality/retrieval.py`

## 2. Evaluating the generator

**Goal**: given good context, produce a grounded, relevant answer, or abstain.

**Test**: isolate the generator by giving it the hand-picked ideal context, not
what the retriever returns. Now retrieval cannot be blamed.

**Metrics**: faithfulness, answer relevancy. Add abstention checks: questions
whose answer is not in the context, where the right output is "I don't know".

**What to vary**: prompt wording, model, temperature, context ordering, how many
chunks are passed in.

**Watch for**: models that ignore context and answer from memory (high
relevancy, low faithfulness), and models that abstain too eagerly.

**Repo**: `evals/quality/generation.py`

## 3. Evaluating the pipeline

**Goal**: retrieval and generation work together.

**Test**: run the real pipeline per question. Score the triad: contextual
relevancy, faithfulness, answer relevancy.

**Diagnosis table**

| Contextual relevancy | Faithfulness | Answer relevancy | Likely cause |
|---|---|---|---|
| low | high | low | Retrieval brought wrong chunks, generator summarized them honestly |
| high | low | any | Generator is making things up despite good context |
| high | high | low | Context is good, answer drifted off the question or padded |
| low | low | low | Both are broken, fix retrieval first |
| high | high | high | Healthy |

**Repo**: `evals/quality/rag_triad.py`

## 4. Evaluating the full application

The whole product, as a user meets it.

**Answer quality**: correctness, completeness and style against ideal answers.
Keep correctness and completeness as separate metrics. They fail independently.

**Safety**: scope, prompt and content leakage, PII, toxicity, with adversarial
datasets. Treat as gates. See the metrics guide for attack types.

**Operations**: latency percentiles (end-to-end and time to first token), cost
per query, success and retry rates. These are measurements, with budgets (SLOs)
instead of ground truth.

**Repo**: `evals/quality/answer_quality.py`, `evals/safety.py`,
`evals/operational.py`

## 5. The evaluation pipeline (workflow)

How the pieces run together over the life of the project.

```
 build golden sets
        |
        v
 run suite on current system  ->  baselines/baseline.json   (the reference)
        |
        v
 change one thing (prompt, k, reranker, model, chunking)
        |
        v
 run suite again              ->  baselines/candidate.json
        |
        v
 compare per metric using rules (direction, gate/guardrail, tolerance)
        |
   +----+----------------+
   |                     |
 PASS                  REVIEW / FAIL
 promote, re-bless      inspect failing cases, fix or reject
 baseline
        |
        v
 deploy, then online monitoring on sampled traffic
        |
        v
 failures found in production become new golden cases
```

Design rules that make this work:

1. **Build the pipeline once and share it.** Every eval in a run measures the
   same object, so baseline and candidate differ only by the change under test.
2. **Change one thing at a time.** Two simultaneous changes cannot be attributed.
3. **Keep metrics separate.** Pooling them lets a regression hide behind an
   improvement elsewhere.
4. **Record provenance.** Each snapshot stores the commit and a prompt hash.
5. **Compare with tolerance.** Judge scores and latency wobble between identical
   runs. Measure that wobble by running the same system twice, and set
   tolerances above it. Otherwise noise shows up as regressions.
6. **Gates for safety, guardrails for quality.** Safety drops block. Quality drops
   go to a person, because trade-offs are sometimes acceptable (slightly lower
   completeness for much lower cost).

**Repo**: `evals/regression/` (`run_suite.py`, `rules.py`, `compare.py`)

## 6. Golden datasets

The suite is only as good as the data.

- Write questions the way users ask, including vague, multi-part and badly
  phrased ones. Include questions the corpus cannot answer.
- Cover every source document and the main question types (definition,
  comparison, how-to, multi-hop).
- Have a human write or at least verify every ideal answer and ideal context.
  Machine-generated sets (see `scripts/synthesize_goldens.py`) are a starting
  draft, not a finished set. Synthetic questions tend to be too clean and too
  closely tied to the chunk they came from.
- Keep a separate dataset per concern. Safety cases are attacks, not questions.
- Version the datasets. A score only means something against a known dataset.
- Grow the set from production: every real failure becomes a test case.
- Size: dozens are enough to catch big regressions, hundreds to detect small ones.

## 7. Using an LLM as judge

LLM judges make evaluation scalable, and they bring their own failure modes.

- **Pin the judge model.** Changing it changes every score.
- **Use explicit criteria.** Tell GEval what to ignore as well as what to score.
- **Read the reasons.** Spot-check failures by hand. Check a sample of passes too.
- **Calibrate against humans.** Label 30 to 50 cases by hand and check the judge
  agrees. If it does not, fix the criteria before trusting any score.
- **Know the biases**: judges favor longer answers, favor their own model
  family's style, and are sensitive to order when comparing two answers.
- **Separate dimensions.** One metric per question. "Is it correct and complete
  and well written" gets mushy scores.

## 8. Offline versus online

| | Offline | Online |
|---|---|---|
| Data | Golden sets | Live traffic |
| Ground truth | Yes | No |
| Metrics | Everything, including reference-based | Reference-free only (relevancy, faithfulness, safety flags) |
| When | Before release, in CI | After release, continuously |
| Catches | Regressions | Drift, unseen query types, new failure modes |

Online eval cannot use correctness or recall because there is no expected
answer. It samples a fraction of traces (judge calls are expensive), scores them,
and writes the scores back as feedback so they can be charted and alerted on.
Add user signals where you have them: thumbs, retries, abandonment, escalation.

**Repo**: `evals/online_monitor.py`

## 9. Putting it into CI

- Fast checks on every pull request: a small subset (retrieval and safety), plus
  unit tests on the pipeline plumbing.
- Full suite before release: run, compare, block on exit code 1.
- Scheduled run (nightly or weekly) on the main branch: catches silent changes
  from model provider updates, since hosted models change underneath you.
- Alerts on online metrics: faithfulness or error rate dropping over a rolling
  window.

## 10. Common mistakes

- Scoring only the final answer, then guessing which component to fix.
- Testing the generator with retrieved context, so retriever errors look like
  generator errors.
- Treating a 0.8 as good or bad without a baseline or a threshold chosen from data.
- Gating on pass rate with small datasets. One borderline case swings it by
  several points.
- Changing the judge, the dataset and the system in one run.
- Ignoring cost and latency until after launch. A 2-point quality gain that
  triples cost is often a bad trade.
- Skipping unanswerable questions. Systems that never say "I don't know" look
  great on answerable-only sets.
- Writing the golden set from the same chunks the retriever indexes, then being
  surprised recall is high.
- No adversarial cases. Safety failures are found only by trying to cause them.

## Quick reference

| Question | Where to look |
|---|---|
| Is the right context retrieved? | retriever eval: recall |
| Is it ranked well? | retriever eval: precision |
| Is the answer made up? | faithfulness |
| Is the answer on topic? | answer relevancy |
| Is it right and complete? | correctness, completeness |
| Does it stay in role and keep secrets? | scope, leakage, toxicity |
| Is it fast, cheap, stable? | latency percentiles, cost per query, success rate |
| Did my change make it worse? | regression compare |
| Is production healthy? | online monitor |
