import os

os.environ["AGENTPAY_LLM"] = "rules"
os.environ["AGENTPAY_TODAY"] = "2026-11-02"  # понедельник
os.environ["AGENTPAY_SECRET_KEY"] = "test-key-" + "0" * 32

import pytest
from fastapi.testclient import TestClient

from app import catalogs, db
from app.trust import payments


@pytest.fixture(autouse=True)
def fresh_state(tmp_path):
    db.use(tmp_path / "agentpay.sqlite3")
    catalogs.reset()
    payments.settings.update(per_operation_limit_rub=30_000, daily_limit_rub=100_000)
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def owner_of(client) -> str:
    """Хеш сессии клиента — так же, как его вычисляет сервер."""
    import hashlib
    from app.main import SESSION_COOKIE
    if SESSION_COOKIE not in client.cookies:
        client.get("/api/health")
    return hashlib.sha256(client.cookies[SESSION_COOKIE].encode()).hexdigest()[:32]


def code_for(client, ch: dict) -> str:
    """Код подтверждения с «устройства» клиента (в продукте — push в приложение банка)."""
    return next(m["code"] for m in client.get("/api/sandbox/device").json()["messages"] if m["challenge_id"] == ch["challenge_id"])
