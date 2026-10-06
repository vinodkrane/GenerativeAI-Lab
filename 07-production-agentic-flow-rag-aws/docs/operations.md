# Operations

## Signals

Logs are JSON lines in CloudWatch Logs (`/ecs/<project>-<env>/api|worker`) with
`request_id`, `query_id`, `document_id`, `tenant_id` and `agent_run_id`. Document and
question text are never logged.

Metrics are emitted as CloudWatch Embedded Metric Format (namespace `LeanAgenticRAG`),
each with a `Service` aggregate and, where relevant, a detail dimension:

| Metric | Meaning |
|---|---|
| `Queries`, `QueryLatency` (use p50/p95), `QueryCostMicroUSD` | query volume, latency, estimated model cost |
| `Abstentions` (`Reason`) | abstention rate and why |
| `CitationValidationFailures` (`Attempt`), `Groundedness` | generator quality |
| `AgentCalls`, `AgentLatency`, `Tokens`, `AgentInvalidOutput` (`Agent`) | agent usage and failures |
| `ToolCalls` (`Tool`) | search and rerank calls |
| `IngestionCompleted` (`Status`), `IngestionFailures` (`Stage`), `IngestionTransientErrors`, `IngestionLatency`, `IngestionTokens` | ingestion outcomes |
| `ProbePassRate`, `ChunksCreated`, `EmbeddingsComputed`/`EmbeddingsCached`, `StaleChunksDeleted`, `OcrDocuments`, `InjectionSuspectChunks` | index quality and work done |
| `AclViolationsBlocked` | must stay 0 (alarmed) |

Queue depth, DLQ size and oldest-message age are native `AWS/SQS` metrics.

The `<project>-<env>` dashboard shows latency, abstentions, queue/DLQ, ingestion outcomes,
probe pass rate, agent calls/tokens and cost.

## Alarms (SNS topic `<project>-<env>-alarms`)

| Alarm | Action |
|---|---|
| ingestion DLQ not empty | follow [DLQ runbook](#dlq) |
| backlog age > 30 min | check worker health/scaling, Bedrock throttling (`retrying` log events) |
| API 5xx > 5 / 5 min | API logs by `request_id`; RDS/OpenSearch health |
| query latency p95 > 20 s | `AgentLatency` by agent; Bedrock throttling; OpenSearch CPU |
| ingestion failures > 3 / 15 min | `ingestion_failed` events: `stage` and `reason` |
| ACL post-filter blocked | the index returned unauthorised chunks; treat as a security incident, check index mappings / filter code |

## Useful Logs Insights queries

```
fields @timestamp, msg, document_id, stage, reason
| filter msg in ["ingestion_failed", "ingestion_attempt_failed"]
| sort @timestamp desc

fields @timestamp, query_id, abstained, reason, llm_calls, tokens, latency_ms
| filter msg = "query_completed"
| stats count(*) as n, avg(latency_ms) as avg_ms, pct(latency_ms, 95) as p95_ms by reason

fields @timestamp, agent, model_id, input_tokens, output_tokens, cost_usd, valid_output
| filter msg = "agent_call"
| stats sum(cost_usd) by agent, model_id
```

## DLQ

A message reaches the DLQ after `max_receive_count` failed deliveries; the document is
already `FAILED` with `error = "retries exhausted: ..."`.

1. Find the cause: filter worker logs by `document_id` for `ingestion_attempt_failed`.
2. Fix it (quota increase, model access, OpenSearch capacity, bug fix + deploy).
3. Either redrive the messages (`python scripts/redrive_dlq.py`, uses SQS
   `StartMessageMoveTask`) **and** reprocess the documents, or just reprocess:
   `POST /documents/{id}/reprocess` (admin) moves `FAILED → RECEIVED` and enqueues work.
   Messages for documents that are already terminal are no-ops.

## NEEDS_REVIEW documents

Probes still failed after the bounded re-chunk. Typical causes: garbled OCR, tables with
no prose, boilerplate. The chunks are indexed and searchable; review and either upload a
better version (`POST /documents/{id}/versions`) or reprocess after changing settings.

## Reindexing and rollback

Use when the chunker, embedding model or agent policy changes (bump `CHUNKER_VERSION` /
`AGENT_POLICY_VERSION` in `config.py` or change `RAG_EMBEDDING_MODEL_ID`).

```bash
lean-rag-reindex status
lean-rag-reindex build --version 2          # builds chunks_v2 from S3; live alias untouched
lean-rag-reindex promote --index chunks_v2  # atomic alias swap; previous index -> RETIRED
lean-rag-reindex rollback                   # swap back to the most recent RETIRED index
```

`build` re-ingests every `INDEXED` document from S3 into the new index (with a catch-up pass
for documents indexed during the build), then evaluates it with deterministic probes against
both the new and live index. It is `READY` only if document recall@5 ≥ 0.8 and not more than
0.05 below the live index; otherwise `REJECTED`, which `promote` refuses (unless `--force`).
Retired indexes are kept for rollback; delete them manually in OpenSearch when no longer needed.
Note: a change of embedding dimension requires the same `RAG_EMBEDDING_DIMENSIONS` for the new
index and the application, so deploy the new setting together with the promotion.

## Scaling

- API: `api_desired_count` (stateless).
- Worker: target tracking on queue backlog between `worker_min_count` and `worker_max_count`.
  Bedrock quotas are usually the real ceiling; watch `retrying` events before adding workers.
- `RAG_LIMITS__EMBEDDING_BATCH_SIZE` controls embedding concurrency per worker.
