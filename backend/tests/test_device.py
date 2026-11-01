"""Код подтверждения доставляется отдельным каналом (в sandbox — «устройство клиента», /api/sandbox/device),
а не в ответе API, который запрашивает подтверждение: иначе тот, кто вызвал checkout — скрипт, интеграция или агент
с доступом к API, — сам и подтверждал бы платёж. В хранилище челленджа код лежит только в виде HMAC."""
import json

from fastapi.testclient import TestClient

from app import db
from app.main import app

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _checkout(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    return o, client.post(f"/api/orders/{o['order_id']}/checkout")


def test_checkout_response_has_no_code(client):
    o, r = _checkout(client)
    ch = r.json()["challenge"]
    assert ch and "sandbox_code" not in ch
    code = client.get("/api/sandbox/device").json()["messages"][0]["code"]
    assert code not in json.dumps(r.json())


def test_code_arrives_on_device_of_same_client(client):
    o, r = _checkout(client)
    ch = r.json()["challenge"]
    push = [m for m in client.get("/api/sandbox/device").json()["messages"] if m["challenge_id"] == ch["challenge_id"]]
    assert len(push) == 1 and len(push[0]["code"]) == 6
    assert TestClient(app).get("/api/sandbox/device").json()["messages"] == []
    ok = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": push[0]["code"]})
    assert ok.status_code == 200
    assert client.get("/api/sandbox/device").json()["messages"] == []  # использованный код больше не показывается


def test_code_not_stored_in_plain_text(client):
    o, r = _checkout(client)
    code = client.get("/api/sandbox/device").json()["messages"][0]["code"]
    stored = " ".join(row[0] for row in db.query("SELECT doc FROM challenges"))
    assert code not in stored
