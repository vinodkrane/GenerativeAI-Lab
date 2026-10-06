# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `auth_mode=local requires RAG_LOCAL_JWT_SECRET (at least 32 characters)` | no `.env` | `make install` (creates `.env` with random secrets) or copy `.env.example` |
| `production cannot use local backends` | `RAG_ENVIRONMENT=prod` with local settings | set all backends to AWS (Terraform does this) |
| 401 on every request | wrong token type or audience | send the Cognito **ID** token (not the access token) for the configured app client |
| 404 for a document you uploaded | different tenant/groups in the token than at upload | check `custom:tenant_id` and groups in the token |
| Document stuck in `PENDING_UPLOAD` | upload never reached S3, or notification missing | check the presigned POST response (403 = size/type mismatch); check the bucket notification targets the queue with prefix `raw/` |
| Document `FAILED` with `scanned PDF needs OCR` | local mode has no Textract | use AWS backends with Bedrock models, or a text PDF |
| Document `FAILED` with `retries exhausted` | throttling/timeouts persisted for all deliveries | see the DLQ runbook in [operations.md](operations.md#dlq) |
| Document `NEEDS_REVIEW` | probes failed after re-chunking | see [operations.md](operations.md#needs_review-documents) |
| Every query abstains with `insufficient_evidence` | nothing indexed for this user, or no live index yet | check document status and ACLs; check `lean-rag-reindex status` |
| Queries abstain with `citation_validation_failed` | generator cites wrong passages or adds numbers | inspect `citation_validation_failed` log events; consider a stronger `RAG_GENERATOR_MODEL_ID` |
| `AccessDeniedException` from Bedrock | model access not enabled or wrong model id/region | enable access in the Bedrock console; check the `eu.` inference profile ids |
| OpenSearch `403` | task role not in the domain access policy or unsigned requests | `RAG_OPENSEARCH_AWS_AUTH=true`; re-apply Terraform |
| OpenSearch mapping error on upsert | embedding dimension differs from the index | `RAG_EMBEDDING_DIMENSIONS` must match the live index; reindex into a new version |
| `make docker-up`: LocalStack exits immediately | newer LocalStack images can require an auth token | keep the pinned image tag, or set `LOCALSTACK_AUTH_TOKEN` in `.env` |
| Terraform: OpenSearch domain creation fails on a new account | service-linked role missing | `aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com` |
| ECS tasks fail with `CannotPullContainerError` | image tag not pushed yet | `make deploy TAG=...` pushes before applying |
