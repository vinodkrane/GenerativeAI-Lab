# --- encryption key for documents, database and search index -------------------------------
resource "aws_kms_key" "data" {
  description             = "${local.name} data at rest (S3, RDS, OpenSearch)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

# --- documents bucket: the source of truth -------------------------------------------------
resource "aws_s3_bucket" "documents" {
  bucket_prefix = "${local.name}-docs-"
  force_destroy = !var.deletion_protection
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "documents" {
  count  = length(var.upload_cors_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.documents.id
  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.upload_cors_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

data "aws_iam_policy_document" "documents_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.documents.arn,
      "${aws_s3_bucket.documents.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "documents" {
  bucket     = aws_s3_bucket.documents.id
  policy     = data.aws_iam_policy_document.documents_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.documents]
}

# --- ingestion queue + DLQ -----------------------------------------------------------------
resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${local.name}-ingestion-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate and redrive
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ingestion" {
  name                       = "${local.name}-ingestion"
  visibility_timeout_seconds = var.queue_visibility_timeout_s
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 345600 # 4 days
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "ingestion_dlq" {
  queue_url = aws_sqs_queue.ingestion_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingestion.arn]
  })
}

data "aws_iam_policy_document" "ingestion_queue" {
  statement {
    sid       = "AllowDocumentsBucket"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingestion.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.documents.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "ingestion" {
  queue_url = aws_sqs_queue.ingestion.id
  policy    = data.aws_iam_policy_document.ingestion_queue.json
}

# Only raw uploads trigger ingestion; the worker's derived/ artifacts never do.
resource "aws_s3_bucket_notification" "documents" {
  bucket = aws_s3_bucket.documents.id
  queue {
    queue_arn     = aws_sqs_queue.ingestion.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "raw/"
  }
  depends_on = [aws_sqs_queue_policy.ingestion]
}
