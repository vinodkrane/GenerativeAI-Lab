# Deployment

## What needs AWS

| Capability | Local (`make run`, tests, `make eval`) | `make docker-up` | AWS |
|---|---|---|---|
| API, worker, supervisors, agents (heuristic policies) | ✅ | ✅ | ✅ |
| PostgreSQL state | SQLite | ✅ Postgres | RDS |
| S3 / SQS | filesystem / SQLite queue | LocalStack | ✅ |
| OpenSearch | in-memory BM25 + cosine | ✅ OpenSearch | ✅ |
| Bedrock LLM agents, Titan embeddings, rerank | ❌ (deterministic local models) | with AWS credentials + `RAG_MODELS=bedrock` | ✅ |
| Textract OCR for scanned PDFs | ❌ (scanned PDFs fail with a clear error) | ❌ | ✅ |
| Cognito auth | ❌ (HS256 dev tokens) | ❌ (dev tokens) | ✅ |

## Prerequisites

- AWS account and credentials with permission to create the resources in `infra/`.
- Terraform ≥ 1.6, AWS CLI v2, Docker with Buildx (the default image platform is `linux/arm64`
  for Graviton; set `cpu_architecture = "X86_64"` and `PLATFORM=linux/amd64` to change).
- **Bedrock model access** in your region for the configured models (defaults: Claude Haiku
  4.5 and Claude Sonnet 4.5 via the `eu.` cross-region inference profiles, Titan Text
  Embeddings V2). Model ids change over time; confirm the ids available to your account in
  the Bedrock console and set `agent_model_id`, `generator_model_id`, `embedding_model_id`.
- First OpenSearch VPC domain in the account only:
  `aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com`

## Provision

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # edit: region, certificate, CIDRs, alarm email
terraform init                                 # add -backend-config=... for remote state
terraform apply -target=aws_ecr_repository.app # the repository must exist before the first push
cd ..
make deploy TAG=v1                             # build, push, terraform apply with image_tag=v1, wait for ECS
```

`scripts/deploy.sh` builds the image, pushes it to ECR (tags are immutable), runs
`terraform apply -var image_tag=<tag>` so task definitions pin that image, and waits for
both services to stabilise. ECS deployment circuit breakers roll back failed deployments.

Plan/apply/destroy directly:

```bash
make tf-plan       # terraform -chdir=infra plan -out=tfplan
make tf-apply      # terraform -chdir=infra apply tfplan
make tf-destroy    # terraform -chdir=infra destroy
```

## Configuration

All runtime configuration is environment variables with the `RAG_` prefix
(`src/lean_rag/config.py`); Terraform sets them on the task definitions. Nested limits use
`RAG_LIMITS__<NAME>`, for example `RAG_LIMITS__MAX_LLM_CALLS_PER_QUERY=4`. The database
password is injected from the RDS-managed Secrets Manager secret as `RAG_DB_PASSWORD`.

Set `RAG_MODEL_PRICES_PER_1K` (JSON: `{"model-id": [input, output]}`) from current Bedrock
pricing so `estimated_cost_usd` and the cost metric are meaningful.

## Users

```bash
POOL=$(terraform -chdir=infra output -raw cognito_user_pool_id)
aws cognito-idp admin-create-user --user-pool-id "$POOL" --username alice@example.com \
  --user-attributes Name=email,Value=alice@example.com Name=email_verified,Value=true \
                    Name=custom:tenant_id,Value=acme
aws cognito-idp create-group --user-pool-id "$POOL" --group-name staff
aws cognito-idp admin-add-user-to-group --user-pool-id "$POOL" --username alice@example.com --group-name staff
```

Clients obtain an **ID token** (SRP flow) and send it as `Authorization: Bearer <token>`.
For CLI testing in a dev environment you can set `enable_password_auth_flow = true` and use
`aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH ...` (keep it off in production).

## Smoke test

```bash
API=$(terraform -chdir=infra output -raw api_url)
curl -s "$API/healthz"
# create a document, upload with the returned presigned POST fields, poll status, then:
curl -s -X POST "$API/query" -H "Authorization: Bearer $ID_TOKEN" \
  -H 'Content-Type: application/json' -d '{"question": "What is the hotel limit?"}'
```

`scripts/demo.py --api "$API"` performs the same flow but mints local tokens, so against AWS
use your Cognito token with the curl commands above.

## Reindexing in AWS

Run the reindex CLI as a one-off task using the worker task definition:

```bash
aws ecs run-task --cluster "$(terraform -chdir=infra output -raw ecs_cluster_name)" \
  --launch-type FARGATE \
  --task-definition "$(terraform -chdir=infra output -raw worker_task_definition)" \
  --network-configuration "awsvpcConfiguration={subnets=[$(terraform -chdir=infra output -json private_subnet_ids | jq -r 'join(",")')],securityGroups=[$(terraform -chdir=infra output -raw app_security_group_id)]}" \
  --overrides '{"containerOverrides":[{"name":"worker","command":["lean-rag-reindex","build","--version","2"]}]}'
```

Then `promote --index chunks_v2` the same way once the build reports `READY`
(see [operations.md](operations.md#reindexing-and-rollback)).

## Teardown

Set `deletion_protection = false` in `terraform.tfvars`, `terraform apply` (to lift RDS,
Cognito and bucket protections), then `make tf-destroy`. This deletes all documents,
indexes and state.
