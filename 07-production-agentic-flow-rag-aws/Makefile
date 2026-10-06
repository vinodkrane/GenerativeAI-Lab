SHELL := /bin/bash
PY ?= python3.12
VENV := .venv
BIN := $(VENV)/bin
TF := terraform -chdir=infra
SUB ?= alice
TENANT ?= acme
USER_GROUPS ?= staff
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null)

.DEFAULT_GOAL := help
.PHONY: help install test test-unit lint format typecheck check eval eval-bedrock run worker token demo \
        docker-build docker-up docker-down audit tf-init tf-validate tf-plan tf-apply tf-destroy deploy clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PY) -m venv $(VENV)

install: $(BIN)/python ## Create .venv and install the app with dev tools
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"
	@test -f .env || (cp .env.example .env && \
	  sed -i.bak "s/^RAG_LOCAL_JWT_SECRET=.*/RAG_LOCAL_JWT_SECRET=$$($(BIN)/python -c 'import secrets;print(secrets.token_hex(32))')/" .env && \
	  sed -i.bak "s/^RAG_LOCAL_UPLOAD_SECRET=.*/RAG_LOCAL_UPLOAD_SECRET=$$($(BIN)/python -c 'import secrets;print(secrets.token_hex(32))')/" .env && \
	  rm -f .env.bak && echo "Created .env with fresh local secrets")

test: ## Run all tests (unit, integration, e2e) - no AWS needed
	$(BIN)/pytest

test-unit: ## Run unit tests only
	$(BIN)/pytest tests/unit

lint: ## Ruff lint + format check
	$(BIN)/ruff check src tests scripts
	$(BIN)/ruff format --check src tests scripts

format: ## Auto-format and fix lint
	$(BIN)/ruff format src tests scripts
	$(BIN)/ruff check --fix src tests scripts

typecheck: ## mypy (strict) on the application package
	$(BIN)/mypy

check: lint typecheck test eval ## Everything CI runs, locally

eval: ## Offline evaluation (local models) with quality gates
	$(BIN)/lean-rag-eval

eval-bedrock: ## Evaluation with Bedrock models (needs AWS credentials + model access)
	$(BIN)/lean-rag-eval --models bedrock

run: ## Run the API with an embedded worker on local backends (http://localhost:8000/docs)
	$(BIN)/lean-rag-api

worker: ## Run a standalone ingestion worker with the current .env
	$(BIN)/lean-rag-worker

token: ## Mint a local dev token: make token SUB=alice TENANT=acme USER_GROUPS=staff,engineering
	@$(BIN)/python scripts/dev_token.py --sub $(SUB) --tenant $(TENANT) --groups $(USER_GROUPS)

demo: ## Upload a sample document and ask a question against a running API
	$(BIN)/python scripts/demo.py

audit: ## Dependency vulnerability scan
	$(BIN)/pip-audit --skip-editable

docker-build: ## Build the application image
	docker build -t lean-agentic-rag:local .

docker-up: ## Start Postgres, OpenSearch, LocalStack (S3+SQS), API and worker
	@test -f .env || (echo "Run 'make install' first (creates .env)"; exit 1)
	docker compose --env-file .env up --build -d
	@echo "API: http://localhost:8000/docs"

docker-down: ## Stop the compose stack and remove volumes
	docker compose down -v

tf-init: ## terraform init (see docs/deployment.md for the remote state backend)
	$(TF) init

tf-validate: ## terraform fmt check + validate
	$(TF) fmt -check -recursive
	$(TF) validate

tf-plan: ## terraform plan
	$(TF) plan -out=tfplan

tf-apply: ## terraform apply the saved plan
	$(TF) apply tfplan

tf-destroy: ## terraform destroy (deletes all data)
	$(TF) destroy

deploy: ## Build, push to ECR and roll the ECS services: make deploy TAG=v1
	@test -n "$(TAG)" || (echo "set TAG=<image tag>"; exit 1)
	./scripts/deploy.sh $(TAG)

clean: ## Remove local state, caches and build output
	rm -rf .data dist build .mypy_cache .ruff_cache .pytest_cache evals/reports
