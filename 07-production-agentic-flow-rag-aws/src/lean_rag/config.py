"""Application configuration.

Every setting comes from environment variables (prefix ``RAG_``) so the same image
runs locally and on ECS. Secrets are never defaults: in AWS they are injected by ECS
from Secrets Manager; locally they come from ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Bump these when the corresponding behaviour changes. They are stored on every chunk and
# index version so a reindex can tell what produced the data it is replacing.
CHUNKER_VERSION = "chunker-v1"
AGENT_POLICY_VERSION = "agents-v1"


class Limits(BaseModel):
    """Hard bounds on every loop, retry and model call. Nothing in the system is unbounded."""

    max_llm_calls_per_query: int = 4  # retrieval plan + evidence assessment + 2 generator attempts
    max_llm_calls_per_ingestion: int = 6
    max_tokens_per_query: int = 12_000
    max_tokens_per_ingestion: int = 20_000
    max_output_tokens: int = 1_024
    max_retries: int = 4  # per external call (throttling / timeouts)
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    max_ingestion_steps: int = 12  # supervisor transitions per document per attempt
    max_rechunk_attempts: int = 1
    max_receive_count: int = 5  # SQS deliveries before the message goes to the DLQ
    max_search_rounds: int = 2
    max_rewritten_queries: int = 3
    retrieval_candidates: int = 30  # per query per search mode, before fusion
    rerank_top_n: int = 8
    max_evidence_chunks: int = 6
    generation_attempts: int = 2  # 1 attempt + 1 bounded regeneration
    embedding_batch_size: int = 16
    max_upload_bytes: int = 25 * 1024 * 1024
    max_document_chars: int = 2_000_000
    max_chunks_per_document: int = 2_000
    verifier_probe_count: int = 5
    verifier_min_pass_rate: float = 0.6
    min_evidence_score: float = 0.15
    model_timeout_s: int = 60
    ocr_timeout_s: int = 300


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    environment: Literal["local", "test", "dev", "prod"] = "local"
    service_name: str = "lean-agentic-rag"
    log_level: str = "INFO"
    aws_region: str = "eu-west-2"

    # --- backends -------------------------------------------------------------------------
    # "local" backends are real implementations intended for development and tests; the AWS
    # backends are what runs in production. Production refuses to start with local backends.
    object_store: Literal["s3", "local"] = "local"
    queue: Literal["sqs", "local"] = "local"
    search: Literal["opensearch", "memory"] = "memory"
    models: Literal["bedrock", "local"] = "local"
    auth_mode: Literal["cognito", "local"] = "local"

    database_url: str = "sqlite:///./.data/rag.db"
    # On ECS the URL is assembled from parts; the password is injected by ECS from the RDS
    # managed secret in Secrets Manager and never appears in Terraform state or task env.
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "rag"
    db_user: str | None = None
    db_password: str | None = None
    local_data_dir: str = "./.data"
    aws_endpoint_url: str | None = None  # LocalStack / custom endpoints for S3 and SQS only
    # Endpoint browsers use for presigned uploads when it differs from aws_endpoint_url
    # (docker-compose: the API reaches "localstack:4566", the host uses "localhost:4566").
    aws_public_endpoint_url: str | None = None

    # --- S3 / SQS ---------------------------------------------------------------------------
    documents_bucket: str = "lean-rag-documents"
    ingestion_queue_url: str = ""
    ingestion_dlq_url: str = ""
    presign_expiry_s: int = 900
    sqs_wait_time_s: int = 20
    sqs_visibility_timeout_s: int = 900

    # --- OpenSearch -------------------------------------------------------------------------
    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_use_ssl: bool = True
    opensearch_aws_auth: bool = True  # SigV4 for Amazon OpenSearch Service
    opensearch_username: str | None = None  # only for local docker OpenSearch
    opensearch_password: str | None = None
    index_alias: str = "chunks_live"
    index_prefix: str = "chunks_v"

    # --- Models -----------------------------------------------------------------------------
    # Cheaper model for agent decisions, stronger model only for answer generation.
    agent_model_id: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    generator_model_id: str = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
    embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    # Optional Bedrock reranker; without it a deterministic lexical reranker is used. Rerank
    # models are not offered in every region, so the reranker has its own region setting.
    rerank_model_arn: str | None = (
        None  # e.g. arn:aws:bedrock:eu-central-1::foundation-model/amazon.rerank-v1:0
    )
    rerank_region: str | None = None
    # USD per 1K tokens: {"model-id": [input, output]}. Configure from current Bedrock pricing.
    model_prices_per_1k: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {
            "eu.anthropic.claude-haiku-4-5-20251001-v1:0": (0.001, 0.005),
            "eu.anthropic.claude-sonnet-4-5-20250929-v1:0": (0.003, 0.015),
            "amazon.titan-embed-text-v2:0": (0.00002, 0.0),
        }
    )

    # --- Auth -------------------------------------------------------------------------------
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    tenant_claim: str = "custom:tenant_id"
    groups_claim: str = "cognito:groups"
    local_jwt_secret: str | None = None  # HS256 secret for local dev tokens only
    local_upload_secret: str | None = None  # signs local "presigned" upload URLs
    public_base_url: str = "http://localhost:8000"
    # Run the ingestion worker as a thread inside the API process (local development only).
    embedded_worker: bool = False

    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.db_host:
            if not (self.db_user and self.db_password):
                raise ValueError("db_host requires db_user and db_password")
            self.database_url = (
                f"postgresql+psycopg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}?sslmode=require"
            )
        if self.environment == "prod":
            local = {
                "object_store": self.object_store == "local",
                "queue": self.queue == "local",
                "search": self.search == "memory",
                "models": self.models == "local",
                "auth_mode": self.auth_mode == "local",
            }
            bad = [name for name, is_local in local.items() if is_local]
            if bad:
                raise ValueError(f"production cannot use local backends: {', '.join(bad)}")
            if not self.ingestion_queue_url or not self.cognito_user_pool_id:
                raise ValueError("production requires ingestion_queue_url and cognito_user_pool_id")
        if self.auth_mode == "local" and len(self.local_jwt_secret or "") < 32:
            raise ValueError("auth_mode=local requires RAG_LOCAL_JWT_SECRET (at least 32 characters)")
        if self.object_store == "local" and len(self.local_upload_secret or "") < 32:
            raise ValueError("object_store=local requires RAG_LOCAL_UPLOAD_SECRET (at least 32 characters)")
        return self

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
