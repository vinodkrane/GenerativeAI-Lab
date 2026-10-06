"""Move messages from the ingestion DLQ back to the source queue after fixing the cause.

Documents whose retries were exhausted are FAILED; reprocess them with
POST /documents/{id}/reprocess (admin) or by redriving their S3 events with this script.

    python scripts/redrive_dlq.py            # uses RAG_* settings (.env or environment)
"""

from __future__ import annotations

from lean_rag.config import get_settings
from lean_rag.storage.objects import aws_client
from lean_rag.storage.queue import LocalQueue


def main() -> None:
    settings = get_settings()
    if settings.queue == "local":
        queue = LocalQueue(
            f"{settings.local_data_dir}/queue.db",
            settings.sqs_visibility_timeout_s,
            settings.limits.max_receive_count,
        )
        print(f"redrove {queue.redrive_dlq()} local messages")
        return
    sqs = aws_client("sqs", settings.aws_region, settings.aws_endpoint_url)
    dlq_arn = sqs.get_queue_attributes(QueueUrl=settings.ingestion_dlq_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    # SQS moves messages back to their original source queue (the redrive allow policy permits it).
    task = sqs.start_message_move_task(SourceArn=dlq_arn, MaxNumberOfMessagesPerSecond=10)
    print(f"started DLQ redrive task {task['TaskHandle']}")


if __name__ == "__main__":
    main()
