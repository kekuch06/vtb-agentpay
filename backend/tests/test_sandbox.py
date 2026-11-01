"""Сценарии sandbox действуют на клиента, который их запустил. Было: «рост цены» и «товар закончился» меняли общий
тестовый каталог процесса — на общем стенде сценарий эксперта ломал заказ другому, а при нескольких процессах
uvicorn срабатывал только в своём процессе."""
from fastapi.testclient import TestClient

from app.main import app

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _offer(client, task, offer_id):
    return next(o["offer"] for o in task["offers"] if o["offer"]["offer_id"] == offer_id)


def test_scenario_affects_only_its_client(client):
    a, b = client, TestClient(app)
    ta = a.post("/api/tasks", json={"text": MAIN}).json()
    rec = ta["recommended_offer_id"]
    price = _offer(a, ta, rec)["total_kop"]
    assert a.post("/api/sandbox/scenario", json={"name": "price_spike", "offer_id": rec}).status_code == 200
    assert a.post("/api/sandbox/scenario", json={"name": "out_of_stock", "offer_id": rec}).status_code == 200
    tb = b.post("/api/tasks", json={"text": MAIN}).json()
    assert tb["recommended_offer_id"] == rec and _offer(b, tb, rec)["total_kop"] == price  # у другого клиента каталог прежний
    ob = b.post("/api/orders", json={"task_id": tb["task_id"], "offer_id": rec}).json()["order"]
    assert b.post(f"/api/orders/{ob['order_id']}/checkout").json()["challenge"] is not None
    ta2 = a.post("/api/tasks", json={"text": MAIN}).json()  # а у запустившего — товар закончился
    assert ta2["recommended_offer_id"] != rec


def test_reset_restores_only_own_catalog(client):
    a, b = client, TestClient(app)
    ta = a.post("/api/tasks", json={"text": MAIN}).json()
    rec = ta["recommended_offer_id"]
    a.post("/api/sandbox/scenario", json={"name": "out_of_stock", "offer_id": rec})
    b.post("/api/sandbox/scenario", json={"name": "out_of_stock", "offer_id": rec})
    a.post("/api/sandbox/scenario", json={"name": "reset"})
    assert a.post("/api/tasks", json={"text": MAIN}).json()["recommended_offer_id"] == rec
    assert b.post("/api/tasks", json={"text": MAIN}).json()["recommended_offer_id"] != rec  # сброс A не трогает B


def test_scenario_survives_restart_and_other_processes(client):
    # Сценарий хранится в базе, а не в памяти процесса: его видит и другой процесс, и тот же после перезапуска.
    from app import catalogs
    ta = client.post("/api/tasks", json={"text": MAIN}).json()
    rec = ta["recommended_offer_id"]
    client.post("/api/sandbox/scenario", json={"name": "out_of_stock", "offer_id": rec})
    catalogs.reset()  # так выглядит другой процесс или перезапуск: каталог в памяти исходный
    assert client.post("/api/tasks", json={"text": MAIN}).json()["recommended_offer_id"] != rec
