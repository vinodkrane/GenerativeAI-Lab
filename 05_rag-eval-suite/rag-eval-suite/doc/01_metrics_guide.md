# Metrics guide

What each metric measures, how it is computed, and what a low score usually means.
The numbers in brackets are the thresholds this repo uses.

Metrics split into four groups: retrieval, generation, safety and operations.
Retrieval and generation fail for different reasons and are fixed in different
places, so they are scored separately.

## Vocabulary

| Term | Meaning |
|---|---|
| Query | The user's question |
| Context / retrieval context | The chunks the retriever returned |
| Ideal context | Chunks a human marked as the correct evidence |
| Actual output | What the generator answered |
| Expected output | The ideal answer written by a human |
| Golden | One reviewed test row (question plus ideal answer or context) |
| Judge | The LLM that scores outputs |

---

## 1. Retrieval metrics

Question asked: did the retriever bring back what the generator needs, and in a
useful order?

### Recall

Of everything that should have been retrieved, how much was?

```
recall = relevant items retrieved / relevant items that exist
```

Example: the answer needs 3 facts. The retrieved chunks contain 2 of them.
Recall = 2/3 = 0.67.

Low recall means the answer cannot be complete however good the generator is.
The usual fixes are a larger `fetch_k`, better chunking, a better embedding
model, or hybrid (keyword + vector) search.

### Precision

Of everything retrieved, how much was useful?

```
precision = relevant items retrieved / items retrieved
```

Example: 5 chunks retrieved, 2 relevant. Precision = 0.4.

Low precision means noise in the prompt. It costs tokens, and it gives the
generator material to get distracted by. Fixes: a reranker, a smaller `top_k`,
a similarity cutoff.

Recall and precision pull against each other. Raising `k` helps recall and hurts
precision. This project over-retrieves (`fetch_k = 10`) to protect recall, then
reranks down to `top_k = 5` to recover precision.

### Contextual Recall (DeepEval) [0.7]

The LLM version of recall. It splits the expected output into statements and
checks, for each one, whether the retrieved context supports it.

```
contextual recall = statements in the expected output supported by the context
                    / statements in the expected output
```

Needs an expected output. It is the right metric when you have no chunk-level
labels, only ideal answers.

### Contextual Precision (DeepEval) [0.7]

Checks whether the relevant chunks are ranked above the irrelevant ones. The
judge marks each retrieved chunk relevant or not, then a rank-weighted precision
is computed so that a relevant chunk at position 1 counts for more than one at
position 5.

Same relevant chunks in a different order give a different score. That is the
point: it measures the ranker, which a plain precision number does not.

### Contextual Relevancy (DeepEval) [0.7]

What share of the retrieved text is relevant to the query?

```
contextual relevancy = relevant statements in the context / all statements in the context
```

Needs no expected output, only the query and the context, so it also works on
production traffic. It is a reference-free stand-in for precision.

### Classical ranking metrics

These need chunk-level labels (which chunk IDs are correct for each query) and no
LLM. They are cheap, deterministic and good for tuning a retriever quickly.

| Metric | Meaning |
|---|---|
| Hit rate @k | Share of queries where at least one correct chunk is in the top k |
| MRR | Mean of 1 / rank of the first correct chunk |
| nDCG @k | Rewards correct chunks that appear higher, handles graded relevance |
| Recall @k, Precision @k | The formulas above, at a fixed cut-off |

This repo uses the LLM-judged versions because the golden sets are labeled with
ideal answers, not chunk IDs. If you label chunk IDs, add the classical metrics
as a fast first check.

---

## 2. Generation metrics

Question asked: given the context, is the answer good?

### Faithfulness (groundedness) [0.7]

Is every claim in the answer supported by the context?

```
faithfulness = claims supported by the context / claims in the answer
```

The judge extracts claims from the answer, then checks each against the context.
It does not care whether the claim is true in the real world, only whether the
context says so. An answer can be factually right and unfaithful if it used
outside knowledge.

Low faithfulness is hallucination, or the model drifting from the context. This
is the most important generation metric for a grounded assistant. Fixes: tighter
prompt, lower temperature, an explicit abstain instruction.

### Answer Relevancy [0.7]

Does the answer address the question?

```
answer relevancy = statements in the answer relevant to the query / statements in the answer
```

Penalizes padding, tangents and non-answers. It says nothing about truth. A
confident, on-topic, wrong answer scores high.

### Correctness [0.7]

Is the answer factually consistent with the ideal answer? Implemented as a
GEval with explicit steps: only contradictions count as errors, extra correct
detail is never penalized, brevity is not penalized.

### Completeness [0.7]

How many key points from the ideal answer does the answer cover?

Kept separate from correctness on purpose. An answer can be correct and
incomplete, or complete and partly wrong. One combined score hides which.

### Style [0.7]

Does the answer read like the intended teaching voice? Reference-free, judged
on tone only. Include it when tone is part of the product, skip it otherwise.

### GEval

A DeepEval metric where you write the criteria. The judge turns your evaluation
steps into a rubric score. Used here for correctness, completeness, style, scope
and protected-information leakage. Useful when no built-in metric fits, but only
as good as the steps you write. Be explicit about what the metric must ignore.

### Other generation metrics worth knowing

| Metric | Meaning |
|---|---|
| Hallucination | Contradiction with the context, close to the inverse of faithfulness |
| Abstention accuracy | Does it say "I don't know" when the context lacks the answer, and answer when it has it? |
| Citation accuracy | Do cited sources actually support the sentence they are attached to? |
| Format adherence | Valid JSON, length limits, required sections |

---

## 3. Application-level view: the RAG triad

Three relationships hold a RAG answer together. Check each one.

```
        query
       /     \
 contextual   answer
 relevancy    relevancy
     /           \
 context ------- answer
      faithfulness
```

| Edge | Metric | If it is low |
|---|---|---|
| query to context | Contextual relevancy | Retrieval problem |
| context to answer | Faithfulness | Generation problem |
| query to answer | Answer relevancy | Answer is off topic |

This is the fastest diagnostic. Low relevancy with high faithfulness: retrieval
is bad, the generator is faithfully summarizing the wrong thing. High relevancy
with low faithfulness: retrieval is fine, the generator is making things up.

---

## 4. Security and safety metrics

These check that the system behaves under adversarial or unusual input. Test
cases here are attacks, not normal questions. Each case has an expected action
(`ANSWER`, `DECLINE` or `PARTIAL`) that the judge treats as ground truth.

### Scope adherence [0.7]

Does the assistant stay in its role? A course assistant should answer course
questions, decline unrelated tasks (write me a poem, debug my SQL), and handle
mixed requests by answering only the in-scope half. Jailbreaks and role-play
instructions are included, because that is how scope usually breaks.

It also fails in the other direction: refusing a legitimate question is a scope
failure too.

### Prompt leakage and protected-content leakage [0.7]

Does the assistant reveal its hidden instructions, dump raw retrieved chunks, or
reproduce large parts of the source material? Attack styles in the dataset
include direct requests, "ignore previous instructions", translation or
continuation tricks, and piece-by-piece extraction over several turns.

Explaining a concept in its own words is allowed. Reproducing the transcript is
not. The judge needs the expected action to tell those apart.

### PII leakage [0.9]

Does the answer repeat personal data (emails, phone numbers, IDs, credentials)
that appeared in the question or context? Higher threshold than the others
because there is little acceptable middle ground.

### Toxicity [lower is better, pass at 0.3 or below]

Does the assistant produce insulting, demeaning or hateful output, including
when the user asks for it directly or through role-play? The only metric here
where a lower score is better.

### Related risks to add for other applications

| Risk | What to test |
|---|---|
| Prompt injection via documents | A retrieved chunk contains "ignore the user and do X". Does the model obey it? |
| Data poisoning | A planted false document in the knowledge base. Does it get cited as truth? |
| Access control | Does retrieval respect per-user permissions, or can one user pull another's documents? |
| Bias | Same question with different names or groups. Do answers differ? |
| Over-refusal | Rate of legitimate questions refused. Tight safety prompts raise it |

Safety metrics are treated as gates in the regression suite. A quality drop gets
reviewed by a person. A safety drop blocks the release.

---

## 5. Operational metrics

Question asked: can the system run reliably, quickly and affordably? None of
these need a dataset or a judge.

### Latency

Time from request to response. Measure many runs and report percentiles.

| Metric | Why |
|---|---|
| p50 | The typical request |
| p95 | What one in twenty users feels. This is the number repo gates on |
| p99 | The worst tail, often retries or cold starts |
| Mean | Reported but misleading, a few slow requests hide in it |

Two kinds of latency matter for a chat UI:

- **End-to-end**: time until the full answer exists. [budget: p95 under 3 s]
- **Time to first token (TTFT)**: time until the first word appears. This is what
  feels fast when streaming. [budget: p95 under 1.2 s]

Break it down by stage (retrieval, rerank, generation) to see where the time
goes. Discard warmup runs so cold start does not distort the numbers. Latency
depends on answer length, so log that too.

### Cost

```
cost per query = input tokens x input price + cached tokens x cached price
                 + output tokens x output price
```

Near-deterministic at temperature 0, so it can be checked before launch. Output
tokens cost several times more than input tokens, so long answers dominate.
Providers cache repeated prompt prefixes, which means the real bill is usually
lower than the estimate. Multiply by expected traffic to get a monthly figure.
Remember judge calls in the eval pipeline are a separate cost.

### Reliability

| Metric | Meaning |
|---|---|
| Success rate | Requests that returned an answer |
| Error rate | Requests that failed after all retries |
| Retry rate | How often a retry was needed. A system that always succeeds on the second try is flaky |

Retries use exponential backoff. On a single machine these read close to 100%.
They become informative under real concurrency and rate limits.

### Others to add in production

Throughput (requests per second at a given latency), rate-limit errors, timeout
rate, vector DB query time, cache hit rate, and token usage per user.

---

## Threshold cheat sheet

| Metric | Higher is better | Threshold here | Role in regression |
|---|---|---|---|
| Contextual recall / precision / relevancy | yes | 0.7 | guardrail (tol 0.05) |
| Faithfulness, answer relevancy | yes | 0.7 | guardrail (tol 0.05) |
| Correctness, completeness, style | yes | 0.7 | guardrail (tol 0.05) |
| Scope, protected leakage | yes | 0.7 | gate (tol 0.02) |
| PII leakage | yes | 0.9 | gate (tol 0.02) |
| Toxicity | no | 0.3 | gate (tol 0.02) |
| e2e p95 latency | no | 3000 ms | guardrail (25%) |
| Cost per query | no | n/a | guardrail (15%) |
| Success rate | yes | n/a | guardrail |

Thresholds are starting points. Set them from your own baseline and the noise you
measure between identical runs.

## Reading a score

- A score is an opinion from a judge model. Read the `reason` text on failing
  cases before drawing conclusions.
- Compare only scores from the same judge model and the same dataset.
- Look at distributions as well as averages. A mean of 0.8 can be ten perfect
  answers and five failures.
- Do not tune thresholds until the tests pass. Tune them once, from data.
