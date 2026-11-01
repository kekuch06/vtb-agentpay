import os

os.environ["AGENTPAY_LLM"] = "rules"
os.environ["AGENTPAY_TODAY"] = "2026-11-02"  # понедельник

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
