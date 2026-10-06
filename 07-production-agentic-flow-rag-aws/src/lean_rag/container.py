"""Composition root: builds every component from ``Settings``. No other module reads config
to decide which backend to use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lean_rag.agents.base import AgentDeps
from lean_rag.agents.chunking_agent import ChunkingAgent
from lean_rag.agents.generator import Generator
from lean_rag.agents.retrieval_agent import RetrievalAgent
from lean_rag.agents.verifier_agent import VerifierAgent
from lean_rag.config import Settings
from lean_rag.ingestion.parsing import TextractOcr
from lean_rag.llm.base import Embedder, LLMClient, ModelGateway, Reranker
from lean_rag.llm.bedrock import BedrockEmbedder, BedrockLLM, BedrockReranker
from lean_rag.llm.local import HashingEmbedder, LexicalReranker
from lean_rag.observability.metrics import Metrics
from lean_rag.retrieval.hybrid import HybridRetriever
from lean_rag.retrieval.index import InMemoryIndex, OpenSearchIndex, SearchIndex
from lean_rag.security.auth import Authenticator, build_authenticator
from lean_rag.storage.db import Repository, create_db_engine
from lean_rag.storage.objects import LocalObjectStore, ObjectStore, S3ObjectStore, aws_client
from lean_rag.storage.queue import LocalQueue, Queue, SqsQueue
from lean_rag.supervisors.ingestion import IngestionSupervisor
from lean_rag.supervisors.query import QuerySupervisor


@dataclass
class Container:
    settings: Settings
    metrics: Metrics
    repo: Repository
    objects: ObjectStore
    queue: Queue
    index: SearchIndex
    embedder: Embedder
    retriever: HybridRetriever
    authenticator: Authenticator
    ingestion: IngestionSupervisor
    query: QuerySupervisor
    agent_deps: AgentDeps


def _opensearch_client(settings: Settings) -> object:
    from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

    auth: object = None
    if settings.opensearch_aws_auth:
        import boto3

        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, settings.aws_region, "es")
    elif settings.opensearch_username:
        auth = (settings.opensearch_username, settings.opensearch_password or "")
    return OpenSearch(
        hosts=[{"host": settings.opensearch_host, "port": settings.opensearch_port}],
        http_auth=auth,
        use_ssl=settings.opensearch_use_ssl,
        verify_certs=settings.opensearch_use_ssl and settings.opensearch_aws_auth,
        connection_class=RequestsHttpConnection,
        timeout=30,
        max_retries=2,
        retry_on_timeout=True,
    )


def build_container(
    settings: Settings,
    *,
    llm_client: LLMClient | None = None,
    emit_metrics: bool = True,
) -> Container:
    """Wire the application. ``llm_client`` lets tests drive the LLM agent path with a scripted
    client; production passes nothing and gets Bedrock when ``models=bedrock``."""
    limits = settings.limits
    metrics = Metrics(settings.service_name, emit=emit_metrics)
    data_dir = Path(settings.local_data_dir)

    repo = Repository(create_db_engine(settings.database_url))
    repo.create_schema()

    objects: ObjectStore
    if settings.object_store == "s3":
        public = settings.aws_public_endpoint_url
        objects = S3ObjectStore(
            settings.documents_bucket,
            aws_client("s3", settings.aws_region, settings.aws_endpoint_url),
            aws_client("s3", settings.aws_region, public) if public else None,
        )
    else:
        assert settings.local_upload_secret
        objects = LocalObjectStore(
            data_dir / "objects", settings.local_upload_secret, settings.public_base_url
        )

    queue: Queue
    if settings.queue == "sqs":
        queue = SqsQueue(
            aws_client("sqs", settings.aws_region, settings.aws_endpoint_url),
            settings.ingestion_queue_url,
            settings.ingestion_dlq_url,
            settings.sqs_visibility_timeout_s,
        )
    else:
        queue = LocalQueue(data_dir / "queue.db", settings.sqs_visibility_timeout_s, limits.max_receive_count)

    index: SearchIndex
    if settings.search == "opensearch":
        index = OpenSearchIndex(
            _opensearch_client(settings), settings.index_alias, settings.embedding_dimensions
        )
    else:
        index = InMemoryIndex(settings.index_alias, data_dir / "index.json")

    embedder: Embedder
    reranker: Reranker
    ocr: TextractOcr | None = None
    if settings.models == "bedrock":
        runtime = aws_client("bedrock-runtime", settings.aws_region, timeout_s=limits.model_timeout_s)
        embedder = BedrockEmbedder(
            runtime,
            settings.embedding_model_id,
            settings.embedding_dimensions,
            limits.embedding_batch_size,
            limits.max_retries,
        )
        reranker = (
            BedrockReranker(
                aws_client("bedrock-agent-runtime", settings.rerank_region or settings.aws_region),
                settings.rerank_model_arn,
                limits.max_retries,
            )
            if settings.rerank_model_arn
            else LexicalReranker()
        )
        llm_client = llm_client or BedrockLLM(runtime)
    else:
        embedder = HashingEmbedder(settings.embedding_dimensions)
        reranker = LexicalReranker()
    if settings.object_store == "s3" and settings.models == "bedrock":
        ocr = TextractOcr(aws_client("textract", settings.aws_region), settings.documents_bucket, limits)

    gateway = ModelGateway(llm_client, settings, metrics) if llm_client is not None else None
    deps = AgentDeps(settings=settings, metrics=metrics, gateway=gateway)
    retriever = HybridRetriever(index, embedder, reranker, limits, metrics)

    ingestion = IngestionSupervisor(
        settings=settings,
        repo=repo,
        objects=objects,
        index=index,
        embedder=embedder,
        retriever=retriever,
        chunking_agent=ChunkingAgent(deps),
        verifier_agent=VerifierAgent(deps),
        metrics=metrics,
        ocr=ocr,
    )
    query = QuerySupervisor(
        settings=settings,
        retriever=retriever,
        retrieval_agent=RetrievalAgent(deps),
        generator=Generator(deps),
        metrics=metrics,
    )
    return Container(
        settings=settings,
        metrics=metrics,
        repo=repo,
        objects=objects,
        queue=queue,
        index=index,
        embedder=embedder,
        retriever=retriever,
        authenticator=build_authenticator(settings),
        ingestion=ingestion,
        query=query,
        agent_deps=deps,
    )
