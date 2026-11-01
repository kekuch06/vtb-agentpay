# Backend VTB AgentPay: FastAPI + доверенная зона. Зависимости ставятся строго по хешам из requirements.txt.
FROM python:3.12.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend

COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY app ./app
COPY eval ./eval

# Непривилегированный пользователь; data — для ключа и SQLite, если PostgreSQL не задан.
RUN useradd --system --uid 10001 --home-dir /srv agentpay && mkdir -p data && chown agentpay data
USER agentpay

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
