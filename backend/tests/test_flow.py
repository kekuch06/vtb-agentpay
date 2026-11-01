"""Сквозной сценарий и атаки на платёжный контур. Ни одна атака не должна привести к платежу."""
from app.trust import payments

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _task(client, text=MAIN):
    r = client.post("/api/tasks", json={"text": text})
    assert r.status_code == 200
    return r.json()


def _order(client, task, offer_id=None):
    r = client.post("/api/orders", json={"task_id": task["task_id"], "offer_id": offer_id or task["recommended_offer_id"]})
    return r


def test_happy_path(client):
    t = _task(client)
    best = next(o for o in t["offers"] if o["offer"]["offer_id"] == t["recommended_offer_id"])
    assert best["passed"]
    assert best["offer"]["brand"] == "Samsung" and best["offer"]["warranty_months"] > 0
    # самый дешёвый вариант без гарантии и вариант с инъекцией не рекомендуются
    assert "DS-77-204" not in t["recommended_offer_id"] and "S9E-X" not in t["recommended_offer_id"]
    o = _order(client, t).json()["order"]
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert all(p["passed"] for p in r["policy"])
    ch = r["challenge"]
    pay = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]})
    assert pay.status_code == 200
    assert pay.json()["payment"]["payee_account"] == "40702810900000000003"  # реквизиты из реестра
    audit = client.get(f"/api/audit?task_id={t['task_id']}").json()
    assert audit["chain"]["ok"]
    assert [e["action"] for e in audit["entries"]][-1] == "payment.succeeded"


def test_prompt_injection_offer_excluded(client):
    t = _task(client)
    inj = next(o for o in t["offers"] if o["offer"]["offer_id"] == "komplekt:KP-SSD-2T-S9E-X")
    assert not inj["passed"] and not inj["checks"][0]["passed"]
    assert _order(client, t, "komplekt:KP-SSD-2T-S9E-X").status_code == 409


def test_cannot_order_failed_offer(client):
    t = _task(client)
    assert _order(client, t, "digistore:DS-77-204").status_code == 409  # без гарантии


def test_no_payment_without_confirmation(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    client.post(f"/api/orders/{o['order_id']}/checkout")
    assert payments.payments == {}


def test_wrong_code_and_lockout(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    wrong = "000000" if ch["sandbox_code"] != "000000" else "111111"
    for _ in range(3):
        r = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": wrong})
        assert r.status_code == 403
    r = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]})
    assert r.status_code == 403
    assert payments.payments == {}


def test_replay_confirmation(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    body = {"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]}
    assert client.post(f"/api/orders/{o['order_id']}/confirm", json=body).status_code == 200
    assert client.post(f"/api/orders/{o['order_id']}/confirm", json=body).status_code == 403
    assert len(payments.payments) == 1


def test_challenge_bound_to_order(client):
    t = _task(client)
    o1 = _order(client, t).json()["order"]
    second = next(x["offer"]["offer_id"] for x in t["offers"] if x["passed"] and x["offer"]["offer_id"] != t["recommended_offer_id"])
    o2 = _order(client, t, second).json()["order"]
    ch1 = client.post(f"/api/orders/{o1['order_id']}/checkout").json()["challenge"]
    r = client.post(f"/api/orders/{o2['order_id']}/confirm", json={"challenge_id": ch1["challenge_id"], "code": ch1["sandbox_code"]})
    assert r.status_code == 403


def test_price_spike_before_checkout(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    client.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": t["recommended_offer_id"]})
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is None
    assert any(p["rule"] == "price_recheck" and not p["passed"] for p in r["policy"])


def test_price_spike_after_confirmation_request(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    client.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": t["recommended_offer_id"]})
    r = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]})
    assert r.status_code == 409
    assert payments.payments == {}


def test_out_of_stock(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    client.post("/api/sandbox/scenario", json={"name": "out_of_stock", "offer_id": t["recommended_offer_id"]})
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is None


def test_payee_swap(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    client.post("/api/sandbox/scenario", json={"name": "payee_swap", "order_id": o["order_id"]})
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    failed = {p["rule"] for p in r["policy"] if not p["passed"]}
    assert {"payee_match", "order_integrity"} <= failed and r["challenge"] is None


def test_payee_swap_after_challenge(client):
    t = _task(client)
    o = _order(client, t).json()["order"]
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    client.post("/api/sandbox/scenario", json={"name": "payee_swap", "order_id": o["order_id"]})
    r = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": ch["sandbox_code"]})
    assert r.status_code in (403, 409)
    assert payments.payments == {}


def test_over_limit(client):
    client.put("/api/settings", json={"per_operation_limit_rub": 10_000})
    t = _task(client)
    o = _order(client, t).json()["order"]
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is None
    assert any(p["rule"] == "per_operation_limit" and not p["passed"] for p in r["policy"])


def test_audit_chain_detects_tampering(client, tmp_path):
    import sqlite3
    from app import audit
    _task(client)
    assert audit.verify_chain()["ok"]
    db = sqlite3.connect(audit.DB_PATH)
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit SET data = '{}' WHERE id = 1")
    db.commit()
    assert not audit.verify_chain()["ok"]


def test_audit_is_append_only(client):
    import sqlite3
    import pytest
    from app import audit
    _task(client)
    db = sqlite3.connect(audit.DB_PATH)
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM audit")
