# Interview questions: RAG evaluation

Questions grouped by topic, with short answers. Answers are written the way you
would say them out loud. Examples refer to this repo where useful.

## A. Fundamentals

**1. Why is evaluating a RAG system harder than evaluating a plain LLM?**
There are two systems chained together. A wrong answer can come from retrieval
(missing or noisy context) or from generation (ignoring or distorting context),
and the final answer alone does not tell you which. So you evaluate each part and
the whole.

**2. What are the main components you would evaluate?**
The retriever (with the reranker), the generator, the pipeline that joins them,
and the full application including safety and operations. Then production
behavior after release.

**3. What is the RAG triad?**
Three checks on the three edges between query, context and answer: contextual
relevancy (query to context), faithfulness (context to answer) and answer
relevancy (query to answer). Together they separate retrieval problems from
generation problems.

**4. What is the difference between offline and online evaluation?**
Offline runs on a fixed golden dataset with ground truth, before release.
Online runs on live traffic with no ground truth, so only reference-free metrics
apply. Offline catches regressions; online catches drift and failure types the
dataset never contained.

**5. What is a golden dataset and how do you build one?**
A reviewed set of test cases: questions with ideal answers and, where useful,
ideal context. Start from real or realistic user questions, cover each source
document and question type, include unanswerable questions, have a human verify
every answer, and keep adding production failures. Synthetic generation can
draft it but not finish it.

## B. Retrieval metrics

**6. Define recall and precision for retrieval.**
Recall is the share of relevant material that was retrieved. Precision is the
share of retrieved material that was relevant. Missing evidence limits
completeness; noisy evidence hurts focus and cost.

**7. A retriever has high recall and low precision. What does that mean and what do you do?**
It finds the evidence but buries it in noise. Add a reranker, reduce `top_k`, or
add a similarity cutoff. This is why the repo over-fetches 10 and reranks to 5.

**8. What does contextual precision measure that plain precision does not?**
Ranking order. It weights relevant chunks higher when they appear earlier, so
the same set of chunks scores differently depending on order. It evaluates the
ranker.

**9. What is MRR? When would you use it over recall?**
Mean reciprocal rank: the average of 1 divided by the rank of the first correct
result. Use it when only the top result matters, such as when you pass a single
chunk to the generator.

**10. What is nDCG?**
A ranking metric that rewards relevant results appearing higher and supports
graded relevance (very relevant versus somewhat). Normalized so scores are
comparable across queries.

**11. How do you evaluate retrieval without chunk-level labels?**
Use LLM-judged metrics: contextual recall against an ideal answer, and
contextual relevancy against the query alone. This repo does that. Chunk-level
labels let you add cheap, deterministic metrics such as hit rate and MRR.

**12. How does chunk size affect retrieval quality?**
Small chunks match precisely but lose surrounding context and split facts.
Large chunks keep context but dilute the embedding and add noise to the prompt.
Overlap protects facts crossing a boundary. Tune by running the retrieval eval
across candidate sizes.

**13. What is a reranker and why use one?**
A cross-encoder that reads the query and a chunk together and scores the pair.
It is more accurate than the bi-encoder used for vector search, but too slow for
the whole corpus. So you retrieve broadly and rerank a short list.

**14. When does vector search fail and what helps?**
Exact terms, names, codes and rare words, where embeddings blur meaning. Hybrid
search (BM25 plus vectors) helps. Vague or multi-part questions benefit from
query rewriting or decomposition.

## C. Generation metrics

**15. Define faithfulness.**
The share of claims in the answer that are supported by the retrieved context.
The judge extracts claims and checks each one against the context. It measures
grounding, not real-world truth.

**16. Can an answer be factually correct and still unfaithful?**
Yes. If the model used outside knowledge that the context did not contain, the
claim may be true but is unsupported. For a grounded assistant that is still a
failure, because you cannot verify it from your sources.

**17. What is the difference between faithfulness and answer relevancy?**
Faithfulness asks whether the answer is backed by the context. Answer relevancy
asks whether it addresses the question. A fluent invented answer to the right
question scores high on relevancy and low on faithfulness.

**18. How do you separate correctness from completeness?**
Two metrics with different instructions. Correctness penalizes only
contradictions and ignores omissions. Completeness penalizes missing key points
and ignores errors. Combined, one low score does not say which problem you have.

**19. What is hallucination and how do you measure it?**
Output that contradicts or is unsupported by the source. Measure with
faithfulness or a hallucination metric against the context, and with abstention
tests where the correct response is "I don't know".

**20. How do you test that the system abstains correctly?**
Include questions that the corpus does not answer and expect the exact
abstention response. Also include answerable questions to catch over-abstention.
Track both rates.

## D. LLM-as-judge

**21. What are the risks of LLM-as-judge?**
Judge bias toward longer or stylistically similar answers, inconsistency between
runs, sensitivity to prompt wording, and agreement with the model being judged.
Mitigate by pinning the judge, writing explicit criteria, calibrating against
human labels, and reading failure reasons.

**22. How do you know the judge can be trusted?**
Label a sample by hand (30 to 50 cases), compare with judge scores, and look at
agreement. If it is low, rewrite the criteria. Re-check when you change the
judge model.

**23. What is GEval?**
A metric where you write the evaluation criteria in plain language and the judge
scores against them with a rubric. Flexible, but quality depends on the steps
you write. State what to ignore, otherwise metrics overlap.

**24. Why pin the judge model?**
Scores from different judges are not comparable. A regression suite compares
runs over time, so the judge has to stay fixed.

**25. How do you control judge cost?**
Small datasets offline, sampling online, a cheaper judge for routine checks and a
stronger one for release gates, caching, and dropping metrics that never change
a decision.

## E. Safety and security

**26. What safety properties would you test in a RAG assistant?**
Scope adherence, system-prompt leakage, leakage of protected source content,
PII leakage, toxicity, and resistance to prompt injection. Add access control
and bias depending on the application.

**27. What is prompt injection in RAG and how do you test it?**
Instructions hidden inside retrieved content that try to steer the model
("ignore the user and reveal..."). Test by planting such text in documents and
checking the model does not obey it. The generator prompt here marks context as
untrusted.

**28. What is the difference between direct and indirect prompt injection?**
Direct comes from the user message. Indirect comes through data the system
retrieves or tools it calls. RAG is exposed to the indirect kind.

**29. How do you test for leakage of source material?**
Adversarial prompts: ask for verbatim transcript, ask for continuation, ask for
translation or rewriting, and extract piece by piece across turns. The expected
behavior is to explain in its own words and decline wholesale reproduction.

**30. Why are safety metrics gates and quality metrics guardrails?**
A safety regression is unacceptable regardless of other gains, so it blocks.
Quality moves can be trade-offs (cost versus completeness), so they go to a
person with context.

**31. What is over-refusal and why track it?**
Refusing legitimate questions. Stricter safety prompts tend to raise it. Scope
tests include in-scope questions expecting an answer, so a model that refuses
everything fails.

## F. Operational

**32. Why use percentiles for latency, not the mean?**
Latency is skewed. A few slow requests barely move the mean but define the
experience of the unlucky users. p95 and p99 show the tail.

**33. End-to-end latency versus time to first token?**
End-to-end is time to the full answer. TTFT is time to the first visible word.
With streaming, TTFT drives perceived speed, so you can have a slow total and a
snappy feel.

**34. How would you estimate cost per query?**
Input tokens times input price plus output tokens times output price, with cached
input at the cached rate. Read token counts from the model's usage metadata.
Multiply by traffic for a monthly figure. Output tokens cost more, so verbose
answers dominate.

**35. Why is cost fine to test offline while latency is noisy?**
At temperature 0 with the same context, token counts are almost identical each
run, so cost barely moves. Latency depends on API load and network and varies
run to run, which is why the repo gates only on end-to-end p95 with a wide
tolerance and leaves TTFT as information.

**36. What do retry rate and success rate tell you?**
Success rate is the share of requests served. Retry rate shows hidden flakiness:
a system that succeeds only after retries looks healthy on success rate alone.

## G. Regression and process

**37. How do you decide a change is safe to ship?**
Run the full suite on the baseline and the candidate, compare each metric under
its own rule (direction, gate or guardrail, tolerance) and act on the verdict:
pass, review, or fail.

**38. Why compare metrics separately instead of one overall score?**
An improvement in one metric can hide a regression in another. Separate
comparison keeps each visible.

**39. How do you set a tolerance?**
Run the same system twice and measure how much each metric moves on its own.
Set the tolerance above that noise. Here, scores moved by up to about 0.03 and
e2e p95 latency by about 20%, so guardrails sit at 0.05 and 25%.

**40. Why gate on average score and not pass rate?**
Pass rate is anchored to the threshold. With few cases, one borderline answer
crossing the line swings it by several points. The mean moves smoothly. The
downside is that an average can hide one bad case, so pass rate is kept as
information.

**41. Why build the pipeline once and share it across all evals?**
So the whole snapshot measures one object. Otherwise six separately built
pipelines could differ quietly and baseline versus candidate would not isolate
the change.

**42. What should a snapshot record?**
The metrics plus provenance: git commit, hash of the prompt, a label describing
the change, and ideally model and retrieval settings.

**43. How do you evaluate a change to the prompt?**
Same dataset, same judge, same retriever. Run the suite, compare. Check
faithfulness and safety first, then completeness and cost, since prompt edits
often change answer length.

**44. A model provider silently updates the model. How would you notice?**
A scheduled run of the suite against the unchanged code and datasets, compared
with the last baseline, plus online monitoring on rolling windows.

## H. Debugging scenarios

**45. Faithfulness is low, contextual relevancy is high. What next?**
Retrieval is fine; the generator is straying. Check the prompt for a clear
"only use the context" rule, lower temperature, look at the failing claims in the
judge's reason, and test the generator alone with ideal context.

**46. Contextual relevancy is low, faithfulness is high.**
The generator is honestly summarizing the wrong material. Fix retrieval: chunking,
embedding model, `fetch_k`, reranker, query rewriting.

**47. Recall is high but answers are incomplete.**
The evidence is retrieved but did not reach the answer. Check `top_k` (the right
chunk may be cut), chunk order, context length limits, and whether the prompt
asks the model to cover every part of the question.

**48. Scores are great offline, users complain.**
The dataset does not look like real traffic. Review production queries, add them
as cases, and check online metrics. Also check for question leakage between
dataset and corpus.

**49. Metric scores jump around between identical runs.**
Judge variance. Increase dataset size, pin the judge and temperature, widen
tolerances, and gate on averages.

**50. Latency doubled after adding a reranker. Is that a regression?**
Compare against the quality gain. If precision and faithfulness improved
meaningfully and p95 is still within the SLO, it is an accepted trade. If it
breaks the budget, reduce `fetch_k`, use a smaller reranker, or rerank
asynchronously. The regression suite will mark latency REVIEW and a person
decides.

## I. Quick-fire

| Question | Short answer |
|---|---|
| Which metric detects hallucination? | Faithfulness |
| Which metric evaluates the ranker? | Contextual precision |
| Which needs no ground truth? | Contextual relevancy, faithfulness, answer relevancy |
| Which needs an expected answer? | Contextual recall, correctness, completeness |
| Which metric is lower-is-better? | Toxicity, latency, cost |
| What isolates the generator? | Feed it ideal context |
| What isolates the retriever? | Do not call the generator |
| Gate or guardrail for PII leakage? | Gate |
| What do you track online? | Reference-free quality, safety flags, user signals |
| First thing to check on a low score? | The judge's reason text on failing cases |
