# --- PostgreSQL: document + workflow state --------------------------------------------------
resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "main" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.db_instance_class
  allocated_storage            = 20
  max_allocated_storage        = 100
  storage_type                 = "gp3"
  storage_encrypted            = true
  kms_key_id                   = aws_kms_key.data.arn
  db_name                      = "rag"
  username                     = "rag_app"
  manage_master_user_password  = true # password lives only in Secrets Manager
  db_subnet_group_name         = aws_db_subnet_group.main.name
  vpc_security_group_ids       = [aws_security_group.db.id]
  parameter_group_name         = aws_db_parameter_group.postgres.name
  multi_az                     = var.db_multi_az
  publicly_accessible          = false
  backup_retention_period      = 7
  auto_minor_version_upgrade   = true
  deletion_protection          = var.deletion_protection
  skip_final_snapshot          = !var.deletion_protection
  final_snapshot_identifier    = var.deletion_protection ? "${local.name}-final" : null
  performance_insights_enabled = false
}

# --- OpenSearch: derived search index (rebuildable from S3) ----------------------------------
# Requires the OpenSearch service-linked role. On a fresh account run once:
#   aws iam create-service-linked-role --aws-service-name opensearchservice.amazonaws.com
resource "aws_opensearch_domain" "main" {
  domain_name    = "${var.project}-${var.environment}"
  engine_version = "OpenSearch_2.17"

  cluster_config {
    instance_type          = var.opensearch_instance_type
    instance_count         = var.opensearch_instance_count
    zone_awareness_enabled = var.opensearch_instance_count > 1
    dynamic "zone_awareness_config" {
      for_each = var.opensearch_instance_count > 1 ? [1] : []
      content {
        availability_zone_count = 2
      }
    }
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = 20
  }

  vpc_options {
    subnet_ids         = var.opensearch_instance_count > 1 ? aws_subnet.private[*].id : [aws_subnet.private[0].id]
    security_group_ids = [aws_security_group.search.id]
  }

  encrypt_at_rest {
    enabled    = true
    kms_key_id = aws_kms_key.data.arn
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-2019-07"
  }

  # IAM-only access (SigV4). Only the two task roles may call the domain.
  access_policies = data.aws_iam_policy_document.opensearch_access.json
}

data "aws_iam_policy_document" "opensearch_access" {
  statement {
    actions   = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete"]
    resources = ["arn:aws:es:${var.aws_region}:${local.account_id}:domain/${var.project}-${var.environment}/*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.api_task.arn, aws_iam_role.worker_task.arn]
    }
  }
}
