.PHONY: install install-backend e2e e2e-local eval-llm run backend frontend build test test-pg lint eval lock up down

install: install-backend
	cd frontend && npm ci

install-backend:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv --require-hashes -r requirements.txt -r requirements-dev.txt

# Переменные из .env в корне (адрес и ключ модели, режим разбора). Уже заданные в окружении не перезаписываются,
# поэтому AGENTPAY_LLM=rules make eval работает и при .env с моделью. make test и make test-pg .env не читают.
DOTENV = if [ -f ../.env ]; then while IFS='=' read -r k v; do case "$$k" in ''|[\#]*) continue;; esac; \
	[ -n "$$(printenv "$$k")" ] || export "$$k=$$v"; done < ../.env; fi;

build:
	cd frontend && npm run build

# Один процесс: API + собранный фронтенд на http://localhost:8000
run: build
	cd backend && $(DOTENV) .venv/bin/uvicorn app.main:app --port 8000

backend:
	cd backend && $(DOTENV) .venv/bin/uvicorn app.main:app --reload --port 8000

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
	cd backend && $(DOTENV) .venv/bin/python -m eval.run

# Метрики с моделью, без сверки и со сверкой кодом. Модель — из окружения или .env:
#   Ollama:    AGENTPAY_LLM=ollama AGENTPAY_LLM_MODEL=qwen2.5:3b
#   DeepSeek:  AGENTPAY_LLM=openai AGENTPAY_LLM_BASE_URL=https://api.deepseek.com AGENTPAY_LLM_MODEL=deepseek-chat AGENTPAY_LLM_API_KEY=…
#   Qwen3 MLX: AGENTPAY_LLM=openai AGENTPAY_LLM_BASE_URL=http://127.0.0.1:8081/v1 AGENTPAY_LLM_MODEL=mlx-community/Qwen3-8B-4bit
# Падает сразу, если модель недоступна: цифры правил не выдаются за цифры модели.
eval-llm:
	cd backend && $(DOTENV) AGENTPAY_EVAL_REQUIRE_LLM=1 AGENTPAY_GROUNDING=off .venv/bin/python -m eval.run
	cd backend && $(DOTENV) AGENTPAY_EVAL_REQUIRE_LLM=1 AGENTPAY_GROUNDING=on .venv/bin/python -m eval.run

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
