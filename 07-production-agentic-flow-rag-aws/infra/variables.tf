variable "project" {
  type    = string
  default = "lean-rag"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "eu-west-2"
}

variable "image_tag" {
  description = "Image tag in the ECR repository to run (set by scripts/deploy.sh)."
  type        = string
  default     = "latest"
}

variable "cpu_architecture" {
  description = "ARM64 (Graviton, cheaper) or X86_64. Must match the platform the image was built for."
  type        = string
  default     = "ARM64"
}

# --- networking ---------------------------------------------------------------------------
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs allowed to reach the public load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on the ALB. Empty = HTTP only (development only)."
  type        = string
  default     = ""
}

variable "upload_cors_origins" {
  description = "Browser origins allowed to POST presigned uploads directly to S3."
  type        = list(string)
  default     = []
}

# --- capacity -------------------------------------------------------------------------------
variable "api_desired_count" {
  type    = number
  default = 2
}

variable "worker_min_count" {
  type    = number
  default = 1
}

variable "worker_max_count" {
  type    = number
  default = 4
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "opensearch_instance_type" {
  type    = string
  default = "t3.small.search"
}

variable "opensearch_instance_count" {
  type    = number
  default = 1
}

variable "deletion_protection" {
  description = "Protect RDS from deletion and keep a final snapshot. Set false for throwaway environments."
  type        = bool
  default     = true
}

# --- models -------------------------------------------------------------------------------
variable "agent_model_id" {
  type    = string
  default = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "generator_model_id" {
  type    = string
  default = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  type    = number
  default = 1024
}

variable "rerank_model_arn" {
  description = "Optional Bedrock rerank model ARN. Empty = built-in lexical reranker."
  type        = string
  default     = ""
}

variable "rerank_region" {
  type    = string
  default = ""
}

# --- ingestion ------------------------------------------------------------------------------
variable "max_receive_count" {
  description = "SQS deliveries before a message moves to the DLQ (also passed to the worker)."
  type        = number
  default     = 5
}

variable "queue_visibility_timeout_s" {
  type    = number
  default = 900
}

variable "enable_password_auth_flow" {
  description = "Allow USER_PASSWORD_AUTH (handy for CLI testing). Keep false in production; clients should use SRP."
  type        = bool
  default     = false
}

# --- operations -------------------------------------------------------------------------------
variable "alarm_email" {
  description = "Optional email subscribed to the alarm SNS topic."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 30
}
