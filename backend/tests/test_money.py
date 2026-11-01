"""Деньги считаются в копейках: сумма платежа совпадает с ценой продавца до копейки."""
from datetime import date

from app import catalogs
from app.trust import payments
from conftest import code_for, owner_of

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"
OFFER = "komplekt:KP-SSD-2T-S9E"


def test_kopecks_are_not_lost(client):
    client.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": OFFER})  # 14 790,00 → 18 487,50 ₽
    # Сценарий действует на этого клиента: цену продавца смотрим в его каталоге.
    merchant_total_kop = catalogs.fetch_offer(OFFER, date(2026, 11, 2), owner_of(client)).total_kop
    assert merchant_total_kop % 100  # в цене есть копейки
    payments.settings["per_operation_limit_rub"] = 50_000
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": OFFER}).json()["order"]
    assert o["amount_kop"] == merchant_total_kop
    ch = client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    assert ch["amount_kop"] == merchant_total_kop
    pay = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": code_for(client, ch)}).json()
    assert pay["payment"]["amount_kop"] == merchant_total_kop


def test_budget_compared_in_kopecks(client):
    # Бюджет «до 18 487 ₽» не покрывает цену 18 487,50 ₽: округление вниз не должно пропускать предложение
    client.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": OFFER})
    t = client.post("/api/tasks", json={"text": "Samsung SSD 2 ТБ с гарантией до 18487 рублей"}).json()
    card = next(x for x in t["offers"] if x["offer"]["offer_id"] == OFFER)
    assert not card["passed"]
