terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Use remote state for anything shared. Example (create the bucket/table first):
  #   terraform init -backend-config="bucket=<state-bucket>" \
  #     -backend-config="key=lean-agentic-rag/terraform.tfstate" \
  #     -backend-config="region=eu-west-2" -backend-config="use_lockfile=true"
  # and uncomment:
  # backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)

  # Strip cross-region inference-profile prefixes ("eu.", "us.", ...) to get model ids.
  model_ids = distinct([
    for id in [var.agent_model_id, var.generator_model_id, var.embedding_model_id] :
    replace(id, "/^(eu|us|apac|global)\\./", "")
  ])
  inference_profiles = [
    for id in [var.agent_model_id, var.generator_model_id] : id if can(regex("^(eu|us|apac|global)\\.", id))
  ]
}
