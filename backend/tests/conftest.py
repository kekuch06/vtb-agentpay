import os

os.environ["AGENTPAY_LLM"] = "rules"
os.environ["AGENTPAY_TODAY"] = "2026-11-02"  # понедельник

import pytest
from fastapi.testclient import TestClient

from app import audit, catalogs
from app.trust import payments


@pytest.fixture(autouse=True)
def fresh_state(tmp_path):
    audit.use_path(tmp_path / "audit.sqlite3")
    catalogs.reset()
    payments.reset()
    payments.settings["per_operation_limit_rub"] = 30_000
    yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)
