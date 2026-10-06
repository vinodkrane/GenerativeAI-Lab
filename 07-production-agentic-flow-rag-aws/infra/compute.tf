resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = !var.deletion_protection
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 20 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.name}/worker"
  retention_in_days = var.log_retention_days
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  app_environment = [for k, v in {
    RAG_ENVIRONMENT               = "prod"
    RAG_LOG_LEVEL                 = "INFO"
    RAG_SERVICE_NAME              = local.name
    RAG_AWS_REGION                = var.aws_region
    RAG_OBJECT_STORE              = "s3"
    RAG_QUEUE                     = "sqs"
    RAG_SEARCH                    = "opensearch"
    RAG_MODELS                    = "bedrock"
    RAG_AUTH_MODE                 = "cognito"
    RAG_DOCUMENTS_BUCKET          = aws_s3_bucket.documents.bucket
    RAG_INGESTION_QUEUE_URL       = aws_sqs_queue.ingestion.url
    RAG_INGESTION_DLQ_URL         = aws_sqs_queue.ingestion_dlq.url
    RAG_SQS_VISIBILITY_TIMEOUT_S  = tostring(var.queue_visibility_timeout_s)
    RAG_LIMITS__MAX_RECEIVE_COUNT = tostring(var.max_receive_count)
    RAG_OPENSEARCH_HOST           = aws_opensearch_domain.main.endpoint
    RAG_OPENSEARCH_PORT           = "443"
    RAG_OPENSEARCH_USE_SSL        = "true"
    RAG_OPENSEARCH_AWS_AUTH       = "true"
    RAG_COGNITO_USER_POOL_ID      = aws_cognito_user_pool.main.id
    RAG_COGNITO_APP_CLIENT_ID     = aws_cognito_user_pool_client.api.id
    RAG_AGENT_MODEL_ID            = var.agent_model_id
    RAG_GENERATOR_MODEL_ID        = var.generator_model_id
    RAG_EMBEDDING_MODEL_ID        = var.embedding_model_id
    RAG_EMBEDDING_DIMENSIONS      = tostring(var.embedding_dimensions)
    RAG_RERANK_MODEL_ARN          = var.rerank_model_arn
    RAG_RERANK_REGION             = var.rerank_region
    RAG_DB_HOST                   = aws_db_instance.main.address
    RAG_DB_NAME                   = aws_db_instance.main.db_name
    RAG_DB_USER                   = aws_db_instance.main.username
    RAG_LOCAL_DATA_DIR            = "/tmp/lean-rag"
  } : { name = k, value = v } if v != ""]

  app_secrets = [{
    name      = "RAG_DB_PASSWORD"
    valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::"
  }]

  container_defaults = {
    image                  = local.image
    essential              = true
    readonlyRootFilesystem = false
    environment            = local.app_environment
    secrets                = local.app_secrets
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([merge(local.container_defaults, {
    name         = "api"
    command      = ["lean-rag-api"]
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  })])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([merge(local.container_defaults, {
    name        = "worker"
    command     = ["lean-rag-worker"]
    stopTimeout = 120
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  })])
}

# --- load balancer -------------------------------------------------------------------------
resource "aws_lb" "api" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  idle_timeout               = 120 # queries can take tens of seconds with Bedrock
}

resource "aws_lb_target_group" "api" {
  name                 = "${local.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "https" {
  count             = var.certificate_arn == "" ? 0 : 1
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  # With a certificate, HTTP only redirects. Without one (dev), HTTP serves the API.
  dynamic "default_action" {
    for_each = var.certificate_arn == "" ? [] : [1]
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
  dynamic "default_action" {
    for_each = var.certificate_arn == "" ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }
}

# --- services ------------------------------------------------------------------------------
resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_min_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count] # owned by autoscaling
  }
}

# Scale workers on queue backlog.
resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.worker_min_count
  max_capacity       = var.worker_max_count
}

resource "aws_appautoscaling_policy" "worker_backlog" {
  name               = "${local.name}-worker-backlog"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 10 # keep the visible backlog around 10 messages
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    customized_metric_specification {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      statistic   = "Average"
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.ingestion.name
      }
    }
  }
}
