.PHONY: install run backend frontend build test eval

install:
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements.txt
	cd frontend && npm install

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

eval:
	cd backend && .venv/bin/python -m eval.run
