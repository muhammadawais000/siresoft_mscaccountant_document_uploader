.DEFAULT_GOAL := help
UV ?= uv
MANAGE = $(UV) run python manage.py

.PHONY: help install services run worker migrate makemigrations test jstest cov lint format typecheck check seed check-ollama up down

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Create .venv with Python 3.12 and install all deps + git hooks
	$(UV) sync
	$(UV) run pre-commit install

services: ## Start postgres + redis only (for running Django on the host)
	docker compose up -d db redis

up: ## Start the full stack (db, redis, web, worker)
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

run: ## Run the dev server on the host
	$(MANAGE) runserver 8000

worker: ## Run a Celery worker on the host
	$(UV) run celery -A config worker --loglevel=INFO --concurrency=1

migrate: ## Apply migrations
	$(MANAGE) migrate

makemigrations: ## Create migrations for document_intake
	$(MANAGE) makemigrations document_intake

test: ## Run the test suite
	$(UV) run pytest

jstest: ## Syntax check of our small JS file (htmx does the rest; no build)
	for f in document_intake/static/document_intake/js/*.js; do node --check $$f || exit 1; done

cov: ## Run tests with coverage for services/ and validators/
	$(UV) run pytest --cov=document_intake --cov-report=term-missing

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-format and fix lint issues
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## mypy on the app package
	$(UV) run mypy document_intake config

check: lint typecheck test jstest ## Everything CI runs

seed: ## Create a demo user and sample documents
	$(MANAGE) seed_demo

check-ollama: ## Verify Ollama connectivity and configured models
	$(MANAGE) check_ollama
