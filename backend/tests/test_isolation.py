"""Изоляция клиентов: поручения, заказы, подтверждения, журнал и настройки доступны только в сессии,
в которой созданы. Раньше любой клиент находил чужой заказ в /api/audit и оплачивал его."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.trust import payments

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


@pytest.fixture
def victim_and_attacker():
    victim, attacker = TestClient(app), TestClient(app)
    t = victim.post("/api/tasks", json={"text": MAIN}).json()
    o = victim.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    return victim, attacker, t, o


def test_audit_shows_only_own_entries(victim_and_attacker):
    victim, attacker, t, o = victim_and_attacker
    seen = attacker.get("/api/audit").json()["entries"]
    assert not any(e["task_id"] == t["task_id"] or e["order_id"] == o["order_id"] for e in seen)
    assert attacker.get(f"/api/audit?task_id={t['task_id']}").json()["entries"] == []
    assert any(e["order_id"] == o["order_id"] for e in victim.get("/api/audit").json()["entries"])


def test_foreign_task_and_order_are_not_found(victim_and_attacker):
    victim, attacker, t, o = victim_and_attacker
    assert attacker.get(f"/api/tasks/{t['task_id']}").status_code == 404
    assert attacker.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).status_code == 404
    assert attacker.post(f"/api/orders/{o['order_id']}/checkout").status_code == 404
    assert attacker.post("/api/sandbox/scenario", json={"name": "payee_swap", "order_id": o["order_id"]}).status_code == 404


def test_foreign_order_cannot_be_confirmed(victim_and_attacker):
    victim, attacker, t, o = victim_and_attacker
    ch = victim.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    r = attacker.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]})
    assert r.status_code == 404
    assert payments.all_payments() == []


def test_limits_are_per_client(victim_and_attacker):
    victim, attacker, t, o = victim_and_attacker
    attacker.put("/api/settings", json={"per_operation_limit_rub": 1_000})
    assert victim.get("/api/settings").json()["per_operation_limit_rub"] == 30_000
    r = victim.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is not None


def test_session_cookie_flags():
    r = TestClient(app).get("/api/health")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
