"""Чтение и запись объектов платёжного контура. Объект хранится как JSON его Pydantic-модели,
поля для поиска и ограничений (одна оплата на заказ и на поручение) — в отдельных столбцах."""
from __future__ import annotations

import json
from datetime import date

from . import db
from .models import Challenge, Order, Payment, Task


def save_task(t: Task) -> None:
    db.execute("INSERT INTO tasks (task_id, created_at, doc) VALUES (?, ?, ?)",
               (t.task_id, t.created_at.isoformat(), t.model_dump_json()))


def get_task(task_id: str) -> Task | None:
    rows = db.query("SELECT doc FROM tasks WHERE task_id = ?", (task_id,))
    return Task.model_validate_json(rows[0][0]) if rows else None


def save_order(o: Order) -> None:
    db.execute("INSERT INTO orders (order_id, task_id, doc) VALUES (?, ?, ?) "
               "ON CONFLICT (order_id) DO UPDATE SET doc = excluded.doc",
               (o.order_id, o.task_id, o.model_dump_json()))


def get_order(order_id: str) -> Order | None:
    rows = db.query("SELECT doc FROM orders WHERE order_id = ?", (order_id,))
    return Order.model_validate_json(rows[0][0]) if rows else None


def save_challenge(ch: Challenge, order_hash: str) -> None:
    """Новый запрос подтверждения; прежние по этому заказу больше не действуют."""
    with db.tx():
        db.execute("UPDATE challenges SET used = 1 WHERE order_id = ?", (ch.order_id,))
        db.execute("INSERT INTO challenges (challenge_id, order_id, used, doc) VALUES (?, ?, 0, ?)",
                   (ch.challenge_id, ch.order_id, json.dumps({"challenge": ch.model_dump(mode="json"), "order_hash": order_hash})))


def get_challenge(challenge_id: str) -> tuple[Challenge, str, bool] | None:
    """(челлендж, хеш заказа на момент запроса, использован ли)."""
    rows = db.query("SELECT doc, used FROM challenges WHERE challenge_id = ?", (challenge_id,))
    if not rows:
        return None
    doc = json.loads(rows[0][0])
    return Challenge.model_validate(doc["challenge"]), doc["order_hash"], bool(rows[0][1])


def use_challenge(challenge_id: str) -> None:
    db.execute("UPDATE challenges SET used = 1 WHERE challenge_id = ?", (challenge_id,))


def insert_payment(p: Payment, bank_day: date) -> None:
    db.execute("INSERT INTO payments (payment_id, order_id, task_id, bank_day, amount_kop, doc) VALUES (?, ?, ?, ?, ?, ?)",
               (p.payment_id, p.order_id, p.task_id, bank_day.isoformat(), p.amount_kop, p.model_dump_json()))


def payments_for_task(task_id: str) -> list[Payment]:
    return [Payment.model_validate_json(r[0]) for r in db.query("SELECT doc FROM payments WHERE task_id = ?", (task_id,))]


def spent_on(bank_day: date) -> int:
    return db.query("SELECT COALESCE(SUM(amount_kop), 0) FROM payments WHERE bank_day = ?", (bank_day.isoformat(),))[0][0]


def all_payments() -> list[Payment]:
    return [Payment.model_validate_json(r[0]) for r in db.query("SELECT doc FROM payments ORDER BY payment_id")]


def clear() -> None:
    """Удаляет поручения, заказы и платежи (не журнал): прогон метрик начинает каждое задание с чистого клиента."""
    with db.tx():
        for table in ("payments", "challenges", "orders", "tasks"):
            db.execute(f"DELETE FROM {table}")
