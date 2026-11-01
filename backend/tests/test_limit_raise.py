"""Лимит на одну покупку: понижение — сразу, повышение — только кодом с устройства клиента.
Было: PUT /api/settings повышал лимит без подтверждения, и тот, кто получил доступ к сессии (скрипт, агент с API),
сам расширял рамки, в которых Policy Engine пропускает платежи."""
from fastapi.testclient import TestClient

from app.main import app


def _code(client, request_id):
    return next(m["code"] for m in client.get("/api/sandbox/device").json()["messages"] if m["challenge_id"] == request_id)


def test_lowering_applies_immediately(client):
    r = client.put("/api/settings", json={"per_operation_limit_rub": 10_000})
    assert r.status_code == 200 and r.json()["per_operation_limit_rub"] == 10_000


def test_raise_requires_code_from_device(client):
    r = client.put("/api/settings", json={"per_operation_limit_rub": 50_000})
    assert r.status_code == 202
    assert client.get("/api/settings").json()["per_operation_limit_rub"] == 30_000  # без кода лимит прежний
    req = r.json()["confirmation"]
    assert req["value"] == 50_000 and "code" not in req  # код — только на устройстве, не в ответе
    ok = client.post("/api/settings/confirm", json={"request_id": req["request_id"], "code": _code(client, req["request_id"])})
    assert ok.status_code == 200 and ok.json()["per_operation_limit_rub"] == 50_000
    again = client.post("/api/settings/confirm", json={"request_id": req["request_id"], "code": "000000"})
    assert again.status_code == 403  # запрос одноразовый


def test_wrong_codes_lock_the_request(client):
    req = client.put("/api/settings", json={"per_operation_limit_rub": 50_000}).json()["confirmation"]
    right = _code(client, req["request_id"])
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(3):
        assert client.post("/api/settings/confirm", json={"request_id": req["request_id"], "code": wrong}).status_code == 403
    assert client.post("/api/settings/confirm", json={"request_id": req["request_id"], "code": right}).status_code == 403
    assert client.get("/api/settings").json()["per_operation_limit_rub"] == 30_000


def test_other_client_cannot_confirm(client):
    req = client.put("/api/settings", json={"per_operation_limit_rub": 50_000}).json()["confirmation"]
    code = _code(client, req["request_id"])
    other = TestClient(app)
    assert other.post("/api/settings/confirm", json={"request_id": req["request_id"], "code": code}).status_code == 404
    assert other.get("/api/sandbox/device").json()["messages"] == []
    assert client.get("/api/settings").json()["per_operation_limit_rub"] == 30_000
