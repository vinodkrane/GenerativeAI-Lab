"""Production AWS adapters exercised against moto (S3, SQS) and botocore Stubber (Bedrock)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from moto import mock_aws

from lean_rag.llm.bedrock import BedrockEmbedder, BedrockLLM, BedrockReranker
from lean_rag.reliability import TransientError
from lean_rag.storage.objects import S3ObjectStore
from lean_rag.storage.queue import SqsQueue

REGION = "eu-west-2"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        yield


def test_s3_store_round_trip_and_presigned_post(aws: None) -> None:
    client = boto3.client("s3", region_name=REGION)
    client.create_bucket(Bucket="docs", CreateBucketConfiguration={"LocationConstraint": REGION})
    store = S3ObjectStore("docs", client)
    store.put("raw/t/d/v1/a.md", b"hello", "text/markdown")
    store.put("derived/t/d/v1/x.json", b"{}")
    assert store.get("raw/t/d/v1/a.md") == b"hello"
    assert store.size("raw/t/d/v1/a.md") == 5
    assert store.size("missing") is None
    assert store.delete_prefix("raw/t/") == 1
    assert store.delete_prefix("derived/t/") == 1
    upload = store.presign_upload("raw/t/d/v1/a.md", "text/markdown", 1000, 60)
    assert upload.method == "POST"
    assert upload.fields["key"] == "raw/t/d/v1/a.md"
    policy = json.loads(__import__("base64").b64decode(upload.fields["policy"]))
    assert ["content-length-range", 1, 1000] in policy["conditions"]


def test_sqs_queue_receive_counts_and_dlq_redrive(aws: None) -> None:
    client = boto3.client("sqs", region_name=REGION)
    dlq_url = client.create_queue(QueueName="dlq")["QueueUrl"]
    dlq_arn = client.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    url = client.create_queue(
        QueueName="ingest",
        Attributes={"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "2"})},
    )["QueueUrl"]
    queue = SqsQueue(client, url, dlq_url, visibility_timeout_s=0)
    queue.send({"hello": "world"})
    first = queue.receive()
    assert first[0].receive_count == 1 and json.loads(first[0].body) == {"hello": "world"}
    second = queue.receive()
    assert second[0].receive_count == 2
    assert queue.receive() == []  # exceeded maxReceiveCount -> moved to DLQ
    assert queue.dlq_depth() == 1
    assert queue.depth() == 0


def test_sqs_delete_removes_message(aws: None) -> None:
    client = boto3.client("sqs", region_name=REGION)
    url = client.create_queue(QueueName="q")["QueueUrl"]
    queue = SqsQueue(client, url, "", visibility_timeout_s=0)
    queue.send({"a": 1})
    queue.delete(queue.receive()[0])
    assert queue.receive() == [] and queue.dlq_depth() == 0


def _runtime() -> tuple[object, Stubber]:
    client = boto3.client(
        "bedrock-runtime", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y"
    )
    return client, Stubber(client)


def test_bedrock_converse_parsing() -> None:
    client, stub = _runtime()
    stub.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": '{"ok": true}'}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 12, "outputTokens": 5, "totalTokens": 17},
            "metrics": {"latencyMs": 10},
        },
        {
            "modelId": "m",
            "system": [{"text": "sys"}],
            "messages": [{"role": "user", "content": [{"text": "hi"}]}],
            "inferenceConfig": {"maxTokens": 50, "temperature": 0.0},
        },
    )
    with stub:
        resp = BedrockLLM(client).complete(system="sys", prompt="hi", model_id="m", max_tokens=50)
    assert resp.text == '{"ok": true}' and resp.input_tokens == 12 and resp.output_tokens == 5


def _body(payload: dict[str, object]) -> StreamingBody:
    raw = json.dumps(payload).encode()
    return StreamingBody(io.BytesIO(raw), len(raw))


def test_bedrock_titan_embeddings_and_cache() -> None:
    client, stub = _runtime()
    stub.add_response(
        "invoke_model", {"body": _body({"embedding": [0.1, 0.2]}), "contentType": "application/json"}
    )
    embedder = BedrockEmbedder(client, "amazon.titan-embed-text-v2:0", 2, batch_size=1, max_retries=0)
    with stub:
        assert embedder.embed(["hello"]) == [[0.1, 0.2]]
        assert embedder.embed(["hello"]) == [[0.1, 0.2]]  # served from cache, no second call
    stub.assert_no_pending_responses()


def test_bedrock_embedding_throttling_is_retried_then_transient() -> None:
    client, stub = _runtime()
    for _ in range(2):
        stub.add_client_error("invoke_model", service_error_code="ThrottlingException", http_status_code=429)
    embedder = BedrockEmbedder(client, "m", 2, batch_size=1, max_retries=1)
    with stub, pytest.raises(TransientError):
        embedder.embed(["x"])


def test_bedrock_rerank_request_and_parsing() -> None:
    client = boto3.client(
        "bedrock-agent-runtime", region_name=REGION, aws_access_key_id="x", aws_secret_access_key="y"
    )
    stub = Stubber(client)
    arn = "arn:aws:bedrock:eu-west-2::foundation-model/amazon.rerank-v1:0"
    stub.add_response(
        "rerank",
        {"results": [{"index": 1, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.1}]},
        {
            "queries": [{"type": "TEXT", "textQuery": {"text": "q"}}],
            "sources": [
                {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": t}}}
                for t in ("a", "b")
            ],
            "rerankingConfiguration": {
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "numberOfResults": 2,
                    "modelConfiguration": {"modelArn": arn},
                },
            },
        },
    )
    with stub:
        assert BedrockReranker(client, arn, 0).rerank("q", ["a", "b"], 5) == [(1, 0.9), (0, 0.1)]
