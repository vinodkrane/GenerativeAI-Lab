#!/usr/bin/env bash
# Build the image, push it to the ECR repository created by Terraform, and roll both ECS
# services. Requires: docker, aws CLI v2, terraform outputs (run `make tf-apply` first).
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
cd "$(dirname "$0")/.."

REPO_URL=$(terraform -chdir=infra output -raw ecr_repository_url)
CLUSTER=$(terraform -chdir=infra output -raw ecs_cluster_name)
REGION=$(terraform -chdir=infra output -raw aws_region)
REGISTRY="${REPO_URL%%/*}"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
# Must match var.cpu_architecture (ARM64 by default -> linux/arm64; X86_64 -> linux/amd64).
docker build --platform "${PLATFORM:-linux/arm64}" -t "$REPO_URL:$TAG" .
docker push "$REPO_URL:$TAG"

# Image tags are immutable in ECR; Terraform pins the task definitions to var.image_tag.
terraform -chdir=infra apply -auto-approve -var "image_tag=$TAG"

for SERVICE in api worker; do
  aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" \
    --services "$(terraform -chdir=infra output -raw "ecs_${SERVICE}_service_name")"
done
echo "Deployed $TAG"
