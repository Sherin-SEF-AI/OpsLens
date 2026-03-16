.PHONY: install dev run mcp-server setup-workspace seed test docker-up docker-down lint \
       db-migrate db-revision db-downgrade celery-worker celery-beat redis postgres \
       dev-services dev-all test-cov type-check security-scan docker-dev \
       frontend-install frontend-build frontend-dev test-webhook

# ── Core ──────────────────────────────────────────────────────────────

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

install-otel:
	pip install -e ".[otel]"

install-all:
	pip install -e ".[dev,otel]"

dev:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

run:
	uvicorn src.main:app --host 0.0.0.0 --port 8000

mcp-server:
	npx -y @notionhq/notion-mcp-server --transport http --port 3100

setup-workspace:
	python scripts/setup_workspace.py

seed:
	python scripts/seed_runbooks.py

test-webhook:
	python scripts/test_webhook.py

# ── Database ──────────────────────────────────────────────────────────

db-migrate:
	alembic upgrade head

db-revision:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

db-downgrade:
	alembic downgrade -1

# ── Task Queue ────────────────────────────────────────────────────────

celery-worker:
	celery -A src.tasks.worker worker --loglevel=info --concurrency=4

celery-beat:
	celery -A src.tasks.worker beat --loglevel=info

# ── Dev Services (Docker) ────────────────────────────────────────────

redis:
	docker run --rm --name opslens-redis -p 6379:6379 -d redis:7-alpine

postgres:
	docker run --rm --name opslens-postgres \
		-e POSTGRES_USER=opslens \
		-e POSTGRES_PASSWORD=opslens \
		-e POSTGRES_DB=opslens \
		-p 5432:5432 -d postgres:16-alpine

dev-services: postgres redis
	@echo "PostgreSQL and Redis started. Waiting for readiness..."
	@sleep 2
	@echo "Dev services ready."

dev-all: dev-services
	@echo "Starting MCP server, backend, and frontend..."
	@$(MAKE) mcp-server &
	@sleep 2
	@$(MAKE) db-migrate
	@$(MAKE) dev &
	@$(MAKE) frontend-dev &
	@wait

# ── Testing ───────────────────────────────────────────────────────────

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=html:htmlcov

# ── Code Quality ──────────────────────────────────────────────────────

lint:
	ruff check src/ tests/

lint-fix:
	ruff check --fix src/ tests/

type-check:
	mypy src/ --ignore-missing-imports

security-scan:
	bandit -r src/ -c pyproject.toml || bandit -r src/

# ── Docker ────────────────────────────────────────────────────────────

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# ── Frontend ──────────────────────────────────────────────────────────

frontend-install:
	cd frontend && npm install

frontend-build:
	cd frontend && npm run build && cp -r dist/ ../src/dashboard/

frontend-dev:
	cd frontend && npm run dev
