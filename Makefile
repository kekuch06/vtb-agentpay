.PHONY: install install-backend e2e e2e-local eval-llm run backend frontend build test test-pg lint eval lock up down

install: install-backend
	cd frontend && npm ci

install-backend:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv --require-hashes -r requirements.txt -r requirements-dev.txt

build:
	cd frontend && npm run build

# Один процесс: API + собранный фронтенд на http://localhost:8000
run: build
	cd backend && .venv/bin/uvicorn app.main:app --port 8000

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest -q

# Те же тесты на PostgreSQL: make test-pg PG=postgresql://agentpay:agentpay@localhost:5432/agentpay_test
test-pg:
	cd backend && AGENTPAY_TEST_DATABASE_URL=$(PG) .venv/bin/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check app eval tests
	cd frontend && npx tsc -b

eval:
	cd backend && .venv/bin/python -m eval.run

# Метрики с локальной моделью: нужен запущенный Ollama с моделью AGENTPAY_LLM_MODEL (по умолчанию qwen2.5:3b)
eval-llm:
	cd backend && AGENTPAY_LLM=ollama AGENTPAY_GROUNDING=off .venv/bin/python -m eval.run
	cd backend && AGENTPAY_LLM=ollama AGENTPAY_GROUNDING=on .venv/bin/python -m eval.run

# Пересборка зафиксированных версий backend после правки requirements*.in
lock:
	cd backend && uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes -o requirements.txt
	cd backend && uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes -o requirements-dev.txt

# Docker Compose: backend, frontend (nginx), PostgreSQL, Ollama
up:
	docker compose up -d --build

down:
	docker compose down

# Сквозные сценарии в браузере (телефон 390 и компьютер 1280) против поднятого стенда (make up)
E2E_BASE_URL ?= http://localhost:8080
e2e:
	cd e2e && npm ci && E2E_BASE_URL=$(E2E_BASE_URL) npm run smoke && E2E_BASE_URL=$(E2E_BASE_URL) npx playwright test

# То же без Docker: локальный сервер на собранном фронтенде, разбор правилами, свежая SQLite
e2e-local: build
	cd e2e && npm ci && npx playwright test
