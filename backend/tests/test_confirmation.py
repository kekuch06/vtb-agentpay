"""Подтверждение платежа: лимит попыток ввода кода нельзя обнулить повторным запросом кода,
отклонённый заказ нельзя «оживить», каждый отказ попадает в журнал."""
from datetime import timedelta

from app.trust import payments
from conftest import code_for

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _order(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    return client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]


def _checkout(client, o):
    return client.post(f"/api/orders/{o['order_id']}/checkout").json()


def _confirm(client, o, ch, code):
    return client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": code})


def _wrong(client, ch):
    return "000000" if code_for(client, ch) != "000000" else "111111"


def test_rejected_order_cannot_get_new_code(client):
    o = _order(client)
    ch = _checkout(client, o)["challenge"]
    for _ in range(3):
        _confirm(client, o, ch, _wrong(client, ch))
    r = _checkout(client, o)
    assert r["challenge"] is None and r["order"]["status"] == "rejected"
    assert payments.all_payments() == []


def test_new_code_does_not_reset_attempts(client):
    o = _order(client)
    ch = _checkout(client, o)["challenge"]
    _confirm(client, o, ch, _wrong(client, ch))
    _confirm(client, o, ch, _wrong(client, ch))
    ch2 = _checkout(client, o)["challenge"]  # «не пришёл код» — запросить новый
    assert ch2 is not None
    code = code_for(client, ch2)
    assert _confirm(client, o, ch2, _wrong(client, ch2)).status_code == 403  # третья неверная попытка по заказу
    assert _confirm(client, o, ch2, code).status_code == 403
    assert payments.all_payments() == []


def test_brute_force_across_checkouts_is_bounded(client):
    o = _order(client)
    guesses = 0
    for _ in range(10):
        ch = _checkout(client, o)["challenge"]
        if ch is None:
            break
        for _ in range(3):
            if _confirm(client, o, ch, _wrong(client, ch)).status_code == 403:
                guesses += 1
    assert guesses <= 3
    assert payments.all_payments() == []


def test_code_resend_is_limited(client):
    o = _order(client)
    issued = [_checkout(client, o)["challenge"] for _ in range(5)]
    assert all(issued[: payments.MAX_CHALLENGES]) and not any(issued[payments.MAX_CHALLENGES:])


def test_policy_rejected_order_stays_rejected(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    client.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": t["recommended_offer_id"]})
    assert _checkout(client, o)["challenge"] is None
    client.post("/api/sandbox/scenario", json={"name": "reset"})
    r = _checkout(client, o)
    assert r["challenge"] is None and r["order"]["status"] == "rejected"


def test_every_rejection_is_audited(client, monkeypatch):
    o = _order(client)
    ch = _checkout(client, o)["challenge"]
    later = payments._now() + payments.CHALLENGE_TTL + timedelta(seconds=1)
    monkeypatch.setattr(payments, "_now", lambda: later)
    assert _confirm(client, o, ch, code_for(client, ch)).status_code == 403
    reasons = [e["data"].get("reason") for e in client.get("/api/audit").json()["entries"] if e["action"] == "confirmation.rejected"]
    assert "expired" in reasons


def test_expired_code_rejected_and_new_code_works(client):
    # Сценарий sandbox «Код истёк» — имитация пяти прошедших минут: правильный код больше не принимается,
    # новый код по тому же заказу проходит, а неверных попыток не добавляется.
    o = _order(client)
    ch = _checkout(client, o)["challenge"]
    code = code_for(client, ch)
    assert client.post("/api/sandbox/scenario", json={"name": "code_expired", "order_id": o["order_id"]}).status_code == 200
    r = _confirm(client, o, ch, code)
    assert r.status_code == 403 and "истёк" in r.json()["detail"]
    ch2 = _checkout(client, o)["challenge"]
    assert _confirm(client, o, ch2, code_for(client, ch2)).status_code == 200


def test_code_expired_scenario_is_per_client(client):
    from fastapi.testclient import TestClient

    from app.main import app
    o = _order(client)
    ch = _checkout(client, o)["challenge"]
    other = TestClient(app)
    assert other.post("/api/sandbox/scenario", json={"name": "code_expired", "order_id": o["order_id"]}).status_code == 404
    assert _confirm(client, o, ch, code_for(client, ch)).status_code == 200
    # без действующего кода сценарий не применяется
    assert client.post("/api/sandbox/scenario", json={"name": "code_expired", "order_id": o["order_id"]}).status_code == 409
