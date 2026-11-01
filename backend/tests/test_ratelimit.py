"""Ограничение частоты: поток поручений нагружает модель, поток изменяющих запросов без cookie открывает новые
сессии. Было: ограничений не было вовсе (REVIEW «Что осталось»)."""
import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(ratelimit, "ENABLED", True)
    monkeypatch.setattr(ratelimit, "TASKS", ratelimit.Window(3, 60))
    monkeypatch.setattr(ratelimit, "TASKS_IP", ratelimit.Window(5, 60))
    monkeypatch.setattr(ratelimit, "SESSIONS_IP", ratelimit.Window(4, 60))


def test_tasks_per_session(limits, client):
    for _ in range(3):
        assert client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code == 200
    r = client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "повторите" in r.json()["detail"]
    other = TestClient(app)
    assert other.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code == 200  # другой клиент не страдает
    assert client.get("/api/settings").status_code == 200  # остальное API работает


def test_tasks_per_ip_across_sessions(limits, monkeypatch):
    # Новая сессия на каждое поручение не обходит ограничение: оно действует и на адрес клиента.
    monkeypatch.setattr(ratelimit, "SESSIONS_IP", ratelimit.Window(100, 60))
    codes = [TestClient(app).post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_new_sessions_per_ip(limits):
    # Изменяющий запрос без cookie открывает сессию: поток таких запросов с одного адреса ограничен.
    statuses = [TestClient(app).put("/api/settings", json={"per_operation_limit_rub": 10_000}).status_code for _ in range(5)]
    assert statuses[:4] == [200] * 4 and statuses[4] == 429


def test_window_slides():
    w = ratelimit.Window(2, 10)
    assert w.hit("k", now=0) is None and w.hit("k", now=1) is None
    assert w.hit("k", now=2) == pytest.approx(8)
    assert w.hit("k", now=10.5) is None  # первое попадание вышло из окна
