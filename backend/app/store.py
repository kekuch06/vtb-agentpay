"""Чтение и запись объектов платёжного контура. Объект хранится как JSON его Pydantic-модели,
поля для поиска и ограничений (одна оплата на заказ и на поручение) — в отдельных столбцах.
Чтение всегда идёт с owner: чужое поручение или заказ выглядят так же, как несуществующие."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from . import db
from .models import Challenge, DeviceMessage, Order, Payment, Task


def save_task(owner: str, t: Task) -> None:
    db.execute("INSERT INTO tasks (task_id, owner, created_at, doc) VALUES (?, ?, ?, ?)",
               (t.task_id, owner, t.created_at.isoformat(), t.model_dump_json()))


def get_task(owner: str, task_id: str) -> Task | None:
    rows = db.query("SELECT doc FROM tasks WHERE task_id = ? AND owner = ?", (task_id, owner))
    return Task.model_validate_json(rows[0][0]) if rows else None


def save_order(owner: str, o: Order) -> None:
    # owner не перезаписывается: заказ остаётся у клиента, который его создал.
    db.execute("INSERT INTO orders (order_id, owner, task_id, doc) VALUES (?, ?, ?, ?) "
               "ON CONFLICT (order_id) DO UPDATE SET doc = excluded.doc WHERE orders.owner = excluded.owner",
               (o.order_id, owner, o.task_id, o.model_dump_json()))


def get_order(owner: str, order_id: str) -> Order | None:
    rows = db.query("SELECT doc FROM orders WHERE order_id = ? AND owner = ?", (order_id, owner))
    return Order.model_validate_json(rows[0][0]) if rows else None


def order_owner(order_id: str) -> str | None:
    """Для прогона метрик: проверка, на чьи реквизиты ушёл платёж."""
    rows = db.query("SELECT owner FROM orders WHERE order_id = ?", (order_id,))
    return rows[0][0] if rows else None


def save_challenge(ch: Challenge, order_hash: str, code_mac: str) -> None:
    """Новый запрос подтверждения; прежние по этому заказу больше не действуют. Код хранится только как HMAC."""
    with db.tx():
        db.execute("UPDATE challenges SET used = 1 WHERE order_id = ?", (ch.order_id,))
        db.execute("INSERT INTO challenges (challenge_id, order_id, used, doc) VALUES (?, ?, 0, ?)",
                   (ch.challenge_id, ch.order_id, json.dumps({"challenge": ch.model_dump(mode="json"),
                                                              "order_hash": order_hash, "code_mac": code_mac})))


def get_challenge(challenge_id: str) -> tuple[Challenge, str, str, bool] | None:
    """(челлендж, хеш заказа на момент запроса, HMAC кода, использован ли)."""
    rows = db.query("SELECT doc, used FROM challenges WHERE challenge_id = ?", (challenge_id,))
    if not rows:
        return None
    doc = json.loads(rows[0][0])
    return Challenge.model_validate(doc["challenge"]), doc["order_hash"], doc["code_mac"], bool(rows[0][1])


def push_to_device(owner: str, m: DeviceMessage) -> None:
    db.execute("INSERT INTO device_inbox (challenge_id, owner, created_at, doc) VALUES (?, ?, ?, ?)",
               (m.challenge_id, owner, datetime.now(timezone.utc).isoformat(), m.model_dump_json()))


def device_messages(owner: str) -> list[DeviceMessage]:
    """Коды по действующим запросам подтверждения этого клиента: оплаты заказов и повышения лимита."""
    rows = db.query("SELECT d.doc FROM device_inbox d LEFT JOIN challenges c ON c.challenge_id = d.challenge_id "
                    "LEFT JOIN limit_requests l ON l.request_id = d.challenge_id AND l.owner = d.owner "
                    "WHERE d.owner = ? AND (c.used = 0 OR l.used = 0) ORDER BY d.created_at", (owner,))
    return [DeviceMessage.model_validate_json(r[0]) for r in rows]


def save_limit_request(owner: str, request_id: str, doc: dict) -> None:
    """Новый запрос на повышение лимита; прежние запросы клиента больше не действуют."""
    with db.tx():
        db.execute("UPDATE limit_requests SET used = 1 WHERE owner = ?", (owner,))
        db.execute("INSERT INTO limit_requests (request_id, owner, used, doc) VALUES (?, ?, 0, ?)",
                   (request_id, owner, json.dumps(doc)))


def get_limit_request(owner: str, request_id: str) -> tuple[dict, bool] | None:
    rows = db.query("SELECT doc, used FROM limit_requests WHERE request_id = ? AND owner = ?", (request_id, owner))
    return (json.loads(rows[0][0]), bool(rows[0][1])) if rows else None


def update_limit_request(request_id: str, doc: dict, used: bool) -> None:
    db.execute("UPDATE limit_requests SET doc = ?, used = ? WHERE request_id = ?", (json.dumps(doc), int(used), request_id))


def expire_challenges(order_id: str, at: datetime) -> int:
    """Только для sandbox: действующие коды по заказу считаются истёкшими (имитация прошедших пяти минут)."""
    n = 0
    with db.tx():
        for cid, raw in db.query("SELECT challenge_id, doc FROM challenges WHERE order_id = ? AND used = 0", (order_id,)):
            doc = json.loads(raw)
            doc["challenge"]["expires_at"] = at.isoformat()
            db.execute("UPDATE challenges SET doc = ? WHERE challenge_id = ?", (json.dumps(doc), cid))
            n += 1
    return n


def use_challenge(challenge_id: str) -> None:
    db.execute("UPDATE challenges SET used = 1 WHERE challenge_id = ?", (challenge_id,))


def insert_payment(owner: str, p: Payment, bank_day: date) -> None:
    db.execute("INSERT INTO payments (payment_id, order_id, task_id, owner, bank_day, amount_kop, doc) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (p.payment_id, p.order_id, p.task_id, owner, bank_day.isoformat(), p.amount_kop, p.model_dump_json()))


def payments_for_task(task_id: str) -> list[Payment]:
    return [Payment.model_validate_json(r[0]) for r in db.query("SELECT doc FROM payments WHERE task_id = ?", (task_id,))]


def spent_on(owner: str, bank_day: date) -> int:
    # PostgreSQL возвращает SUM(BIGINT) как numeric (Decimal): в API он уходил дробным числом, а JSON-ответ падал.
    return int(db.query("SELECT COALESCE(SUM(amount_kop), 0) FROM payments WHERE owner = ? AND bank_day = ?",
                        (owner, bank_day.isoformat()))[0][0])


def get_settings(owner: str) -> dict:
    rows = db.query("SELECT doc FROM settings WHERE owner = ?", (owner,))
    return json.loads(rows[0][0]) if rows else {}


def put_settings(owner: str, values: dict) -> None:
    db.execute("INSERT INTO settings (owner, doc) VALUES (?, ?) ON CONFLICT (owner) DO UPDATE SET doc = excluded.doc",
               (owner, json.dumps(values)))


MAX_SANDBOX_EVENTS = 50  # на клиента: список не растёт бесконечно от повторных сценариев


def sandbox_events(owner: str | None) -> list[tuple[str, str]]:
    """Сценарии sandbox клиента по порядку: (сценарий, предложение)."""
    if not owner:
        return []
    rows = db.query("SELECT doc FROM sandbox WHERE owner = ?", (owner,))
    return [tuple(e) for e in json.loads(rows[0][0])] if rows else []


def add_sandbox_event(owner: str, name: str, offer_id: str) -> None:
    with db.tx():
        events = [list(e) for e in sandbox_events(owner)][-(MAX_SANDBOX_EVENTS - 1):] + [[name, offer_id]]
        db.execute("INSERT INTO sandbox (owner, doc) VALUES (?, ?) ON CONFLICT (owner) DO UPDATE SET doc = excluded.doc",
                   (owner, json.dumps(events)))


def clear_sandbox(owner: str) -> None:
    db.execute("DELETE FROM sandbox WHERE owner = ?", (owner,))


def all_payments() -> list[Payment]:
    return [Payment.model_validate_json(r[0]) for r in db.query("SELECT doc FROM payments ORDER BY payment_id")]


def clear() -> None:
    """Удаляет поручения, заказы и платежи (не журнал): прогон метрик начинает каждое задание с чистого клиента."""
    with db.tx():
        for table in ("payments", "device_inbox", "challenges", "limit_requests", "orders", "tasks", "settings", "sandbox"):
            db.execute(f"DELETE FROM {table}")
