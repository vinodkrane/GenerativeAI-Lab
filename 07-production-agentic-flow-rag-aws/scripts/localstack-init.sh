#!/usr/bin/env bash
# Mirrors the Terraform wiring: bucket -> S3 event notification -> SQS queue with a DLQ.
set -euo pipefail
REGION=eu-west-2
BUCKET=lean-rag-documents

awslocal s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint=$REGION
DLQ_URL=$(awslocal sqs create-queue --queue-name lean-rag-ingestion-dlq --query QueueUrl --output text)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal sqs create-queue --queue-name lean-rag-ingestion --attributes "{
  \"VisibilityTimeout\": \"120\",
  \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"
}"
QUEUE_ARN=$(awslocal sqs get-queue-attributes --queue-url http://localhost:4566/000000000000/lean-rag-ingestion --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal s3api put-bucket-notification-configuration --bucket "$BUCKET" --notification-configuration "{
  \"QueueConfigurations\": [{
    \"QueueArn\": \"$QUEUE_ARN\",
    \"Events\": [\"s3:ObjectCreated:*\"],
    \"Filter\": {\"Key\": {\"FilterRules\": [{\"Name\": \"prefix\", \"Value\": \"raw/\"}]}}
  }]
}"
# Browser uploads from http://localhost:8000-hosted tools need CORS on the bucket.
awslocal s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["POST"],"AllowedHeaders":["*"]}]}'
echo "LocalStack resources ready"
