"""Каталоги продавцов — недоверенный источник: некорректная карточка отбрасывается, а не попадает в платёж
и не роняет поиск по остальным продавцам."""
import pytest

from app import catalogs

TEXT = "Samsung 990 EVO 2TB"


def _offer_ids(task):
    return {o["offer"]["offer_id"] for o in task["offers"]}


@pytest.mark.parametrize("field,value", [("price_rub", -5000), ("price_rub", 0), ("price_rub", "15990"), ("warranty_months", -1)])
def test_invalid_values_excluded(client, field, value):
    catalogs._state["techmart"]["items"][0][field] = value
    t = client.post("/api/tasks", json={"text": TEXT}).json()
    assert "techmart:TM-1001" not in _offer_ids(t)
    assert client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": "techmart:TM-1001"}).status_code == 409
    assert any("отброшено" in s["detail"] for s in t["trace"])


def test_negative_delivery_excluded(client):
    catalogs._state["komplekt"]["catalog"][0]["delivery_hours"] = -48
    catalogs._state["digistore"]["products"][0]["shipping"]["eta_days"] = -1
    t = client.post("/api/tasks", json={"text": TEXT}).json()
    assert not {"komplekt:KP-SSD-2T-S9E", "digistore:DS-77-201"} & _offer_ids(t)


def test_malformed_card_does_not_break_search(client):
    del catalogs._state["digistore"]["products"][0]["cost"]
    catalogs._state["komplekt"]["catalog"][1] = "мусор"
    r = client.post("/api/tasks", json={"text": TEXT})
    assert r.status_code == 200
    ids = _offer_ids(r.json())
    assert "digistore:DS-77-201" not in ids and "techmart:TM-1001" in ids and "komplekt:KP-SSD-2T-S9E" in ids


def test_card_broken_after_search_is_not_payable(client):
    t = client.post("/api/tasks", json={"text": "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"}).json()
    assert t["recommended_offer_id"] == "komplekt:KP-SSD-2T-S9E"
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    catalogs._find_raw(t["recommended_offer_id"])["price_kop"] = -100
    r = client.post(f"/api/orders/{o['order_id']}/checkout").json()
    assert r["challenge"] is None
