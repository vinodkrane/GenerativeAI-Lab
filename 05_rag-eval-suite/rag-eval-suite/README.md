# rag-eval-suite

A small RAG application and the test suite built around it. The app is a teaching
assistant that answers questions about an 8-session course on LLM evaluation,
using only the session transcripts. The suite measures it from every angle that
matters before shipping: retrieval quality, answer quality, safety, cost, speed,
and whether a change made things better or worse.

The point of the project is the evaluation, not the chatbot. The app is kept
small so each part can be tested on its own.

## How the app works

```
question
   |
   v
vector search (Chroma, OpenAI embeddings, fetch_k = 10)
   |
   v
cross-encoder rerank  ->  top_k = 5 chunks
   |
   v
generator (gpt-4o-mini, grounded-only prompt)
   |
   v
answer, or "I don't have enough information in the course material to answer that."
```

Transcripts are cleaned of timestamps, split into 1000-character chunks with 150
overlap, and stored in a local Chroma database that is built on first run.

## Project layout

```
rag-eval-suite/
├── app/                       the system under test
│   ├── config.py              models, chunk sizes, k values, paths
│   ├── vector_store.py        load transcripts, chunk, embed, persist
│   ├── retriever.py           vector search + cross-encoder rerank
│   ├── generator.py           prompt and LLM call (plain and streaming)
│   ├── pipeline.py            retrieve -> generate, returns query/context/answer
│   └── chat_ui.py             Streamlit front end
│
├── data/transcripts/          session_01.vtt ... session_08.vtt
│
├── datasets/                  golden sets, one per eval
│   ├── retrieval.json         question + ideal answer, for recall/precision
│   ├── faithfulness.json      question + hand-picked ideal context
│   ├── correctness.json       question + ideal answer, for the final answer
│   ├── scope.json             in-scope, out-of-scope and mixed requests
│   ├── leakage.json           prompt, course-content and PII extraction attempts
│   ├── toxicity.json          attempts to make the bot hostile
│   └── synthetic_draft.json   machine-generated draft, needs human review
│
├── evals/
│   ├── common.py              dataset loading, per-metric summaries
│   ├── quality/
│   │   ├── retrieval.py       retriever alone
│   │   ├── generation.py      generator alone, fed ideal context
│   │   ├── rag_triad.py       whole pipeline, RAG triad metrics
│   │   └── answer_quality.py  correctness, completeness, style
│   ├── safety.py              scope, leakage (protected + PII), toxicity
│   ├── operational.py         latency, cost, reliability
│   ├── online_monitor.py      scores sampled live traces from LangSmith
│   └── regression/
│       ├── run_suite.py       runs everything, writes a snapshot
│       ├── rules.py           direction, gate/guardrail, tolerance per metric
│       └── compare.py         baseline vs candidate -> PASS / REVIEW / FAIL
│
├── baselines/                 snapshots (baseline.json is committed)
├── scripts/                   dataset drafting, chunk export, DeepEval quickstart
├── doc/                       concept guides, strategy, interview questions
├── Makefile
└── pyproject.toml
```

## How the tests are structured

The suite follows the structure of the system. Each layer tests one thing, so
when a number drops you know where to look.

| Layer | File | What it checks | Metrics | Needs |
|---|---|---|---|---|
| Retriever | `evals/quality/retrieval.py` | Does the right context come back, ranked well? | Contextual recall, contextual precision | LLM judge, `retrieval.json` |
| Generator | `evals/quality/generation.py` | Given good context, is the answer grounded? | Faithfulness, answer relevancy | LLM judge, `faithfulness.json` |
| Pipeline | `evals/quality/rag_triad.py` | Do retrieval and generation work together? | Contextual relevancy, faithfulness, answer relevancy | LLM judge |
| Application | `evals/quality/answer_quality.py` | Is the final answer right, complete and well explained? | Correctness, completeness, style (GEval) | LLM judge, `correctness.json` |
| Safety | `evals/safety.py` | Does it stay in role and protect what it should? | Scope adherence, protected-info leakage, PII leakage, toxicity | LLM judge, three datasets |
| Operations | `evals/operational.py` | Is it fast, affordable and dependable? | p50/p95/p99 latency, TTFT, cost per query, success/error/retry rate | No judge, no dataset |
| Online | `evals/online_monitor.py` | Is production behaving like the tests said? | Triad metrics on sampled traffic | LangSmith traces |
| Regression | `evals/regression/` | Did this change make things worse? | All of the above, compared to a baseline | Two snapshots |

The generator test deliberately bypasses the retriever and passes in the
hand-picked context. That is what makes it a test of the generator and not of
the whole system. The pipeline test then lets the real retriever back in.

More detail in [`doc/`](doc/).

## Setup

Python 3.11+ and an OpenAI API key.

```bash
git clone <this repo> && cd rag-eval-suite
python -m venv .venv && source .venv/bin/activate
pip install -e .            # or: uv sync
cp .env.example .env        # add OPENAI_API_KEY
```

The first run downloads a small reranker model (about 80 MB) and builds the
vector store in `chroma_store/`. Embedding the eight transcripts costs a few
cents. After that the store is reused.

LangSmith keys are optional. Without them the app and offline evals still work;
only `online_monitor.py` needs them.

## Running things

Run everything from the project root.

```bash
make app            # chat UI at localhost:8501

make retrieval      # retriever only
make generation     # generator only
make triad          # full pipeline, RAG triad
make answer         # correctness / completeness / style
make safety         # scope, leakage, toxicity
make ops            # latency, cost, reliability
```

Each of these prints DeepEval's own report followed by a per-metric summary.
Without `make`, use the module form, for example
`python -m evals.quality.retrieval`.

Judge-based evals call the OpenAI API several times per test case. A full run is
cheap for this dataset size (15 cases per set) but not free.

## Regression workflow

The suite exists to answer one question: is the new version safe to ship?

```bash
# 1. record the current system as the baseline
make baseline

# 2. change something (prompt, fetch_k, reranker, chunk size, model)

# 3. measure the new version
python -m evals.regression.run_suite --label "fetch_k=20"

# 4. compare
make compare
```

`compare` exits with 0 (PASS), 2 (REVIEW) or 1 (FAIL), so it can sit in CI.

- **Gate**: safety metrics. Any drop beyond a small tolerance fails the run.
- **Guardrail**: quality, latency, cost, reliability. A drop beyond tolerance
  needs a human decision.
- **Info**: everything else is recorded but does not affect the verdict.

Tolerances in `evals/regression/rules.py` come from running the same pipeline
twice and measuring how much the numbers moved by themselves. Without that, noise
from the judge model and the API shows up as false regressions.

Snapshots record the git commit and a hash of the generator prompt, so a silent
prompt edit is visible in the report.

## Datasets

All golden sets are small (15 rows) and were written or reviewed by hand against
the transcripts. `synthetic_draft.json` is the output of
`scripts/synthesize_goldens.py`; treat it as raw material. Its `source` fields
are marked `TODO-verify` and it is not used by any eval until reviewed.

To add a case, append a row to the matching file in `datasets/`. The schema is
visible in the existing rows.

## Configuration

Everything tunable lives in `app/config.py`: chunk size and overlap, embedding
and reranker models, `FETCH_K` / `TOP_K`, generator model, judge model and the
default threshold. Changing a value there changes the app and the evals together.

The judge model matters. Scores are only comparable between runs that used the
same judge, which is why it is pinned in one place.

## Known limits

- 15 cases per dataset is enough to catch large regressions and not much more.
  Small moves in a score are within noise; see the tolerances.
- Safety gates use the average score, so one bad answer among many clean ones can
  slip through. `pass_rate` is recorded alongside as an info metric.
- Latency numbers are single-user. They say nothing about behavior under load.
- Cost uses list prices written in `evals/operational.py`. Check them before
  trusting a budget.
- The judge is an LLM and has its own mistakes. Read the reasons DeepEval prints
  for failing cases before changing the system.

## Docs

- [`doc/01_metrics_guide.md`](doc/01_metrics_guide.md): what each metric means and how it is computed
- [`doc/02_rag_eval_strategy.md`](doc/02_rag_eval_strategy.md): how to evaluate a RAG system end to end
- [`doc/03_interview_questions.md`](doc/03_interview_questions.md): questions and answers on RAG evaluation
