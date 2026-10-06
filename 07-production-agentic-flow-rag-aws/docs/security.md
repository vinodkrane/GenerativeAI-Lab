# Security

## Identity

- Production: Cognito **ID tokens** verified in `security/auth.py` with RS256 via the pool's
  JWKS, checking issuer, audience (app client), expiry and `token_use == "id"`. HS256
  tokens are rejected (algorithm pinning).
- Identity comes only from the token: `sub`, `custom:tenant_id` (required, path-safe
  pattern) and `cognito:groups`. No request schema accepts a tenant or owner; unknown
  request fields are rejected (HTTP 422).
- `custom:tenant_id` is immutable and excluded from the app client's `write_attributes`,
  so users cannot change their tenant. Users are created by administrators only.
- Local development uses HS256 tokens signed with `RAG_LOCAL_JWT_SECRET`; `environment=prod`
  refuses to start with local auth or any other local backend.

## Authorization (default deny)

- Every document has `tenant_id`, `owner_id`, `allowed_groups`, `allowed_users`. Each chunk
  carries `tenant_id` and `acl_principals` (`user:<sub>`, `group:<name>`), copied at
  ingestion.
- `AccessFilter.for_user()` is created in the query supervisor **before** any model call. An
  empty tenant or principal set raises. Every index read method requires the filter, and
  both the BM25 and kNN queries carry `tenant_id` + `acl_principals` filters
  (`tests/unit/test_security.py` asserts the exact query bodies).
- Hits are re-checked in code after search; a mismatch is blocked, counted
  (`AclViolationsBlocked`) and alarmed.
- Document endpoints return 404 for documents the caller cannot read, so existence is not
  disclosed. Only owners/admins can upload versions or delete; only admins can reprocess.
- Uploaders can share only with groups they belong to (admins: any group in their tenant).

### What the LLM cannot do

| Attempt | Why it fails |
|---|---|
| Change ACL filters / pick another tenant | Agent schemas have no such fields and `extra="forbid"`; the filter is built from the token and passed separately |
| Unrestricted search | No index method without an `AccessFilter` exists |
| Query rewriting that drops constraints | Rewrites are plain strings; the original question is always searched; the filter is not part of the query |
| Destructive operations / tool calls | Agents have no tools. Delete and reprocess are HTTP endpoints with code-side authorization |
| Select evidence it was not shown | Unknown evidence labels are ignored |

Tests proving unauthorized users cannot retrieve other users' or tenants' documents:
`tests/integration/test_query_flow.py::test_unauthorized_users_cannot_retrieve_documents`,
`tests/e2e/test_api_e2e.py::test_acl_is_enforced_over_http`, and the `acl` category in the
evaluation (`acl_leaks` must be 0).

## Prompt injection

Documents and questions are untrusted.

1. **Structural**: untrusted text is placed only inside `<untrusted_document>` / `<evidence>`
   fences; any fence-like tags inside the text are neutralised so a document cannot close its
   block. System prompts state that fenced text is data.
2. **No capabilities**: agents cannot act; outputs are schema-validated; authorization is out of
   reach (above).
3. **Parsing**: HTML scripts, styles, templates and comments are dropped.
4. **Detection as a signal**: chunks matching injection patterns are flagged
   (`injection_suspect`, `InjectionSuspectChunks` metric), not deleted.
5. **Output checks**: the deterministic validator rejects uncited/unsupported sentences and
   any output containing the system-prompt canary; the extractive baseline skips injected
   sentences.

Adversarial cases (ignore instructions, reveal system prompt, delete document, call admin
tool, return another document's content) are in `evals/cases.jsonl` (category `injection`)
and in the integration tests.

## Data protection

- S3: SSE-KMS (customer-managed key with rotation), versioning, public access block,
  TLS-only bucket policy, presigned POST restricted to key, type and size.
- RDS: KMS-encrypted storage, `rds.force_ssl=1`, client `sslmode=require`, password managed
  by RDS in Secrets Manager and injected by ECS (never in Terraform variables or task env).
- OpenSearch: encryption at rest (KMS), node-to-node TLS, HTTPS-only (TLS 1.2+), VPC-only,
  IAM-signed requests from the two task roles only.
- SQS: SSE (SQS-managed keys; compatible with S3 notifications).
- Logs: no document text or question text is logged — only ids, sizes, counts and timings.

## IAM (least privilege)

| Role | Can |
|---|---|
| execution | pull from ECR, write logs, read the one DB secret |
| api task | presign `raw/*` uploads, delete `raw/*` + `derived/*` (document delete), use the data key, send to the ingestion queue, query/delete in the OpenSearch domain, invoke the configured Bedrock models |
| worker task | read `raw/*` + `derived/*`, write `derived/*`, consume the queue, index into the domain, invoke the configured Bedrock models, run Textract text detection |

Textract text detection does not support resource-level permissions, so that statement uses
`*`. `bedrock:Rerank` likewise, and is only granted when a rerank model is configured.

## Scanner exceptions

`.trivyignore` lists deliberate exceptions with reasons (public ALB, HTTPS egress to AWS APIs,
configurable ingress CIDRs, HTTP listener only without a certificate).
