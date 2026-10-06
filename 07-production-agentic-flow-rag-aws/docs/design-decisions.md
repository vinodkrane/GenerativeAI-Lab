# Design decisions

Short ADR-style records. Each lists the alternative(s) rejected and why.

### 1. Deterministic supervisors, LLM agents only for judgment
**Decision:** supervisors are plain Python state machines; four agents return validated
decisions. **Rejected:** an LLM orchestrator/planner choosing tools — harder to bound, test
and secure, and the control flow here is fixed. **Consequence:** every path is unit-testable
without a model; the LLM can make a step better or worse but cannot change which steps run.

### 2. No agent framework (LangGraph, Strands)
**Decision:** a dictionary of state handlers plus PostgreSQL state. **Rejected:** LangGraph
and Strands — the ingestion graph is linear with one bounded loop, and durable state already
lives in PostgreSQL + S3; a framework would add a second state store and dependency churn
without simplifying anything.

### 3. ECS Fargate (one image, two services)
**Rejected:** Lambda + Step Functions — splits workflow state, 15-minute limit for OCR-heavy
documents, more moving parts. EKS — operational overhead. App Runner — weaker VPC/IAM story
for OpenSearch and RDS.

### 4. PostgreSQL for workflow state
**Decision:** RDS PostgreSQL with compare-and-set transitions and leases. **Rejected:**
DynamoDB (would work, but listing/reporting by status/tenant and the audit trail are simpler
in SQL, and the brief specified RDS); Step Functions execution history as state.

### 5. S3 as source of truth, OpenSearch as derived data
Raw files and every stage's output (`parsed.json`, `plan.json`, `chunks-*.json`,
`embeddings-*.json`) are stored in S3. Retries resume without recomputation, and any index
version can be rebuilt. **Rejected:** storing chunks only in OpenSearch (no clean rebuild,
no reprocessing without re-embedding).

### 6. Hybrid search with RRF in application code
**Decision:** separate BM25 and kNN requests, fused with Reciprocal Rank Fusion in Python.
**Rejected:** OpenSearch search pipelines/normalisation processors — version-specific and
harder to test; doing it in code makes local and AWS behaviour identical.

### 7. Optional Bedrock reranker
A lexical reranker is the default; Bedrock Rerank is enabled by setting
`RAG_RERANK_MODEL_ARN` (and `RAG_RERANK_REGION`, since rerank models are not offered in
every region). Keeps the default deployment within commonly available models.

### 8. Authorization as an immutable filter object
**Decision:** `AccessFilter` from the token, required by every index method, re-checked
after search, and absent from every agent schema. **Rejected:** document-level
post-filtering only (leaks via ranking and wastes candidates); per-tenant indexes (operational
cost grows with tenants; still needs group ACLs within a tenant).

### 9. Duplicate detection keyed on content **and** audience
Identical bytes shared with a different audience are indexed separately, because chunks
carry ACL principals. Collapsing them would either over-share or under-share.

### 10. Probe-based index verification
Verification runs the real retrieval path on probes for sampled chunks; a probe passes if its
chunk ranks in the top third of the document (max top 5), so tiny documents cannot pass
trivially. One bounded re-chunk, then `NEEDS_REVIEW` (still searchable). **Rejected:**
count-only checks (prove nothing about retrievability); unbounded re-chunk loops.

### 11. Structured generator output + deterministic validation
The generator returns sentences with citation ids, which makes claim-level validation (citation
present, supported, numbers present, no canary) cheap and deterministic. One regeneration
with feedback, then abstain. **Rejected:** LLM-as-judge on the hot path — cost, latency and
another model that can be manipulated.

### 12. Heuristic policies alongside LLM policies
They make the system runnable and testable offline, provide a safe fallback for invalid model
output, and serve as the evaluation baseline. **Trade-off:** two implementations per agent;
each heuristic is a few lines.

### 13. Cheaper model for agents, stronger model for the answer
Agent decisions are short classification/selection tasks (Haiku-class); answer generation
uses a stronger model (Sonnet-class). Both are configuration.

### 14. EMF metrics instead of PutMetricData
Metrics are log lines; no extra API calls or permissions on the request path, and CloudWatch
computes percentiles.

### 15. Schema via `create_all`, not migrations
Three tables, created idempotently at startup. **Trade-off:** schema changes need care;
introduce Alembic when the first breaking change arrives rather than carrying it now.

### 16. One NAT gateway, single-node OpenSearch by default
Cost-conscious defaults for a starting environment, with variables to scale to multi-AZ.
Documented in [aws-infrastructure.md](aws-infrastructure.md).
