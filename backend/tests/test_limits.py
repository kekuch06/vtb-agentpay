"""Дневной лимит считается по банковскому дню (по умолчанию московское время), а не по дате UTC."""
from datetime import datetime, timezone

from app.models import Payment
from app.trust import payments

MAIN = "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"


def _earlier_payment(at: datetime, amount: int) -> None:
    payments.payments["pay_prev"] = Payment(payment_id="pay_prev", order_id="ord_prev", task_id="task_prev",
                                            amount_rub=amount, payee_account="40702810900000000001", status="succeeded", created_at=at)


def _daily_rule(client, monkeypatch, now: datetime):
    monkeypatch.setattr(payments, "_now", lambda: now)
    t = client.post("/api/tasks", json={"text": MAIN}).json()
    o = client.post("/api/orders", json={"task_id": t["task_id"], "offer_id": t["recommended_offer_id"]}).json()["order"]
    return next(p for p in client.post(f"/api/orders/{o['order_id']}/checkout").json()["policy"] if p["rule"] == "daily_limit")


def test_yesterday_moscow_does_not_count(client, monkeypatch):
    # 23:00 МСК 1 ноября и 01:30 МСК 2 ноября — одна дата UTC, но разные банковские дни
    _earlier_payment(datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc), 90_000)
    assert _daily_rule(client, monkeypatch, datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc))["passed"]


def test_same_moscow_day_counts(client, monkeypatch):
    # 00:30 МСК и 03:30 МСК 2 ноября — один банковский день, хотя даты UTC разные
    _earlier_payment(datetime(2026, 11, 1, 21, 30, tzinfo=timezone.utc), 90_000)
    assert not _daily_rule(client, monkeypatch, datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc))["passed"]
