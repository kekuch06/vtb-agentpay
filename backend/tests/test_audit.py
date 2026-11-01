"""Журнал и целостность заказа против злоумышленника с доступом на запись к базе, но без ключа сервера.
Раньше цепочка была SHA-256 без ключа: после правки записи её пересчитывали целиком, и проверка проходила."""
import hashlib
import json

from app import audit, store
from app.trust import payments
from conftest import code_for, drop_audit_guard, owner_of, raw_db

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"
COLS = ["ts", "actor", "zone", "action", "owner", "task_id", "order_id", "data"]


def _paid(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    assert client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": code_for(client, ch)}).status_code == 200


def test_rewritten_chain_without_key_is_detected(client):
    _paid(client)
    assert audit.verify_chain()["ok"]
    con = raw_db()
    drop_audit_guard(con)
    prev = "0" * 64
    for r in con.execute(f"SELECT id, {', '.join(COLS)} FROM audit ORDER BY id").fetchall():
        row = dict(zip(COLS, r[1:]))
        if row["action"] == "payment.succeeded":
            row["data"] = json.dumps({"amount_kop": 100}, ensure_ascii=False)  # «платёж был на 1 рубль»
        h = hashlib.sha256((prev + json.dumps(row, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        con.execute("UPDATE audit SET data = ?, prev_hash = ?, hash = ? WHERE id = ?", (row["data"], prev, h, r[0]))
        prev = h
    assert not audit.verify_chain()["ok"]


def test_order_tampered_in_db_is_detected(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    me = owner_of(client)
    order = store.get_order(me, o["order_id"])
    order.sku = "KP-SSD-2T-S9E-X"  # подмена товара в заказе с пересчётом хеша без ключа
    order.order_hash = hashlib.sha256(
        f"{order.order_id}|{order.task_id}|{order.offer_id}|{order.merchant_id}|{order.payee_account}|{order.sku}|{order.amount_kop}".encode()
    ).hexdigest()
    store.save_order(me, order)
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is None and any(p["rule"] == "order_integrity" and not p["passed"] for p in r["policy"])
    assert payments.all_payments() == []


def test_journal_view_does_not_rescan_whole_bank(client, monkeypatch):
    # Было: каждый GET /api/audit пересчитывал HMAC всего журнала банка (1,3 с на 100 тыс. записей) и отдавал любому
    # клиенту общее число записей всех клиентов.
    _paid(client)
    full = []
    real = audit.verify_chain
    monkeypatch.setattr(audit, "verify_chain", lambda: full.append(1) or real())
    first = client.get("/api/audit").json()
    assert first["chain"] == {"ok": True}  # без числа записей и головы цепочки
    for _ in range(5):
        client.get("/api/audit")
    assert len(full) <= 1  # полная проверка — не на каждый запрос


def test_new_record_tampering_is_found_immediately(client):
    _paid(client)
    assert audit.chain_ok()  # голова цепочки проверена
    client.post("/api/tasks", json={"text": MAIN})  # новые записи после проверенной головы
    con = raw_db()
    drop_audit_guard(con)
    last = con.execute("SELECT max(id) FROM audit").fetchone()[0]
    con.execute("UPDATE audit SET data = ? WHERE id = ?", ('{"text": "подмена"}', last))
    assert not audit.chain_ok()


def test_old_record_tampering_is_found_by_periodic_full_check(client, monkeypatch):
    _paid(client)
    assert audit.chain_ok()
    con = raw_db()
    drop_audit_guard(con)
    first = con.execute("SELECT min(id) FROM audit").fetchone()[0]
    con.execute("UPDATE audit SET data = ? WHERE id = ?", ('{"text": "подмена"}', first))
    assert audit.chain_ok()  # проверка новых записей правку старой не видит — для этого есть полная
    monkeypatch.setattr(audit, "FULL_EVERY", 0)  # подошло время полной проверки
    audit.chain_ok()  # запускает её в фоне, ответ не ждёт
    for _ in range(100):
        if not audit._chain_cache.get("running"):
            break
        import time
        time.sleep(0.02)
    assert not client.get("/api/audit").json()["chain"]["ok"]
    assert not audit.chain_ok(wait=True)
