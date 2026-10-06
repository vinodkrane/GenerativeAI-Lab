output "aws_region" {
  value = var.aws_region
}

output "api_url" {
  value = var.certificate_arn == "" ? "http://${aws_lb.api.dns_name}" : "https://${aws_lb.api.dns_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_api_service_name" {
  value = aws_ecs_service.api.name
}

output "ecs_worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "ingestion_queue_url" {
  value = aws_sqs_queue.ingestion.url
}

output "ingestion_dlq_url" {
  value = aws_sqs_queue.ingestion_dlq.url
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.main.endpoint
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.api.id
}

output "worker_task_definition" {
  description = "Use with `aws ecs run-task` for one-off jobs such as `lean-rag-reindex`."
  value       = aws_ecs_task_definition.worker.arn
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}
