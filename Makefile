.PHONY: install dev run mcp-server setup-workspace seed test docker-up docker-down lint

install:
	pip install -e .

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

test:
	pytest tests/ -v

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

frontend-install:
	cd frontend && npm install

frontend-build:
	cd frontend && npm run build && cp -r dist/ ../src/dashboard/

frontend-dev:
	cd frontend && npm run dev
