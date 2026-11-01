"""Параллельные запросы. Синхронные эндпоинты FastAPI исполняются в пуле потоков, поэтому пары «проверка → изменение»
должны быть атомарными. Задержки внутри критической секции только расширяют окно гонки, которое без них
открывается переключением потоков или вводом-выводом."""
import time
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.trust import payments
from conftest import code_for  # noqa: E402

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _checked_out(client):
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    return o, client.post(f"/api/orders/{o['order_id']}/checkout").json()["challenge"]


def _slow(monkeypatch, name, delay=0.02):
    orig = getattr(payments, name)

    def wrapper(*a, **k):
        r = orig(*a, **k)
        time.sleep(delay)
        return r
    monkeypatch.setattr(payments, name, wrapper)


def test_parallel_code_guessing_is_bounded(client, monkeypatch):
    o, ch = _checked_out(client)
    wrong = [f"{i:06d}" for i in range(30) if f"{i:06d}" != code_for(client, ch)][:20]
    _slow(monkeypatch, "order_hash")
    with ThreadPoolExecutor(20) as ex:
        details = list(ex.map(lambda code: client.post(
            f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": code}).json()["detail"], wrong))
    compared = [d for d in details if "неверный код" in d or "превышено" in d]
    assert len(compared) <= payments.MAX_CODE_ATTEMPTS, details


def test_parallel_confirms_respect_daily_limit(client, monkeypatch):
    payments.settings["daily_limit_rub"] = 40_000
    pending = [_checked_out(client) for _ in range(5)]
    _slow(monkeypatch, "evaluate", 0.05)
    with ThreadPoolExecutor(5) as ex:
        list(ex.map(lambda x: client.post(f"/api/orders/{x[0]['order_id']}/confirm",
                                          json={"challenge_id": x[1]["challenge_id"], "code": code_for(client, x[1])}), pending))
    assert sum(p.amount_kop for p in payments.all_payments()) <= 40_000 * 100


def test_parallel_confirm_same_code_pays_once(client, monkeypatch):
    o, ch = _checked_out(client)
    _slow(monkeypatch, "evaluate")
    with ThreadPoolExecutor(10) as ex:
        codes = list(ex.map(lambda _: client.post(f"/api/orders/{o['order_id']}/confirm",
                                                  json={"challenge_id": ch["challenge_id"], "code": code_for(client, ch)}).status_code, range(10)))
    assert codes.count(200) == 1 and len(payments.all_payments()) == 1


def test_state_survives_restart(client):
    o, ch = _checked_out(client)
    db.use(db.current())  # перезапуск процесса: соединение открывается заново, в памяти ничего не осталось
    r = client.post(f"/api/orders/{o['order_id']}/confirm", json={"challenge_id": ch["challenge_id"], "code": code_for(client, ch)})
    assert r.status_code == 200
    db.use(db.current())
    assert [p.order_id for p in payments.all_payments()] == [o["order_id"]]
    assert client.get(f"/api/tasks/{o['task_id']}").status_code == 200
