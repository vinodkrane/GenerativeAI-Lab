data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# --- execution role: pull image, write logs, read the DB secret -----------------------------
resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.main.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "db-secret"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# --- shared statements ---------------------------------------------------------------------
locals {
  bedrock_resources = concat(
    [for id in local.model_ids : "arn:aws:bedrock:*::foundation-model/${id}"],
    [for id in local.inference_profiles : "arn:aws:bedrock:${var.aws_region}:${local.account_id}:inference-profile/${id}"],
    var.rerank_model_arn == "" ? [] : [var.rerank_model_arn],
  )
  opensearch_arn = "${aws_opensearch_domain.main.arn}/*"
}

# --- API task role: presign uploads, query, delete documents ---------------------------------
data "aws_iam_policy_document" "api_task" {
  statement {
    sid       = "PresignRawUploadsAndDelete"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.documents.arn}/raw/*", "${aws_s3_bucket.documents.arn}/derived/*"]
  }
  statement {
    sid       = "ListForDeletion"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
  }
  statement {
    sid       = "DocumentKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
  statement {
    sid       = "ReprocessRequests"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingestion.arn, aws_sqs_queue.ingestion_dlq.arn]
  }
  statement {
    sid       = "Search"
    actions   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpDelete"]
    resources = [local.opensearch_arn]
  }
  statement {
    sid       = "Models"
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_resources
  }
  dynamic "statement" {
    for_each = var.rerank_model_arn == "" ? [] : [1]
    content {
      sid       = "Rerank"
      actions   = ["bedrock:Rerank"]
      resources = ["*"] # the Rerank action is not scoped to resources; the model itself is above
    }
  }
}

resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "api_task" {
  name   = "api"
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}

# --- worker task role: read raw, write derived, index, OCR -----------------------------------
data "aws_iam_policy_document" "worker_task" {
  statement {
    sid       = "ReadDocuments"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/raw/*", "${aws_s3_bucket.documents.arn}/derived/*"]
  }
  statement {
    sid       = "WriteDerivedArtifacts"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/derived/*"]
  }
  statement {
    sid       = "HeadMissingObjects"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
  }
  statement {
    sid       = "DocumentKey"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
  statement {
    sid = "ConsumeQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.ingestion.arn, aws_sqs_queue.ingestion_dlq.arn]
  }
  statement {
    sid       = "Index"
    actions   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete"]
    resources = [local.opensearch_arn]
  }
  statement {
    sid       = "Models"
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_resources
  }
  dynamic "statement" {
    for_each = var.rerank_model_arn == "" ? [] : [1]
    content {
      sid       = "Rerank"
      actions   = ["bedrock:Rerank"]
      resources = ["*"] # the Rerank action is not scoped to resources; the model itself is above
    }
  }
  statement {
    sid       = "Ocr"
    actions   = ["textract:StartDocumentTextDetection", "textract:GetDocumentTextDetection"]
    resources = ["*"] # Textract does not support resource-level permissions for these actions
  }
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "worker"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}
