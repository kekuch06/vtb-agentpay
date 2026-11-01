"""Фаззинг API по схеме OpenAPI (schemathesis): на любой запрос, допустимый и недопустимый по схеме, сервер отвечает
не 5xx. Ошибка 500 на пользовательском вводе — это либо непойманный дефект разбора, либо утечка внутреннего состояния."""
import os

import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.generation import GenerationMode

from app.main import app

# Схема берётся из приложения: по HTTP она отдаётся только с AGENTPAY_API_DOCS=1.
schema = schemathesis.openapi.from_dict(app.openapi())
schema.app = app
SCALE = int(os.getenv("AGENTPAY_HYPOTHESIS_SCALE", "1"))
schema.config.generation.update(modes=[GenerationMode.POSITIVE, GenerationMode.NEGATIVE])


@schema.parametrize()
@settings(max_examples=60 * SCALE, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_never_answers_5xx(case):
    response = case.call()
    case.validate_response(response, checks=[not_a_server_error])


def _real_objects():
    """Сессия клиента с настоящими поручением, заказом и запросом кода: фаззинг тел запросов доходит до логики,
    а не останавливается на 404 «не найдено»."""
    from fastapi.testclient import TestClient

    from app import catalogs
    from app.main import SESSION_COOKIE
    catalogs.reset()  # сценарии sandbox из прошлых примеров меняют общий тестовый каталог
    c = TestClient(app)
    t = c.post("/api/tasks", json={"text": "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"}).json()
    o = c.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    ch = c.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]
    return c.cookies[SESSION_COOKIE], {"task_id": t["task_id"], "order_id": o["order_id"],
                                        "offer_id": o["offer_id"], "challenge_id": ch["challenge_id"]}


@schema.parametrize()
@settings(max_examples=60 * SCALE, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_on_real_objects_never_answers_5xx(case):
    token, ids = _real_objects()
    for name in list(case.path_parameters or {}):
        if name in ids:
            case.path_parameters[name] = ids[name]
    if isinstance(case.body, dict):
        for k in ids.keys() & case.body.keys():
            case.body[k] = ids[k]
    response = case.call(headers={"Cookie": f"agentpay_session={token}"})
    case.validate_response(response, checks=[not_a_server_error])
