"""Доверенная зона: сервис заказов, Policy Engine, подтверждение пользователем и платёжный sandbox.

Ни одна функция здесь не принимает текст модели. Заказ собирается из повторно запрошенной карточки
продавца и реквизитов из доверенного реестра; платёж исполняется только по подтверждённому челленджу.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from .. import audit, catalogs
from ..models import Challenge, Order, Payment, PolicyResult, Task
from .verifier import _rub

ORDER_TTL = timedelta(minutes=15)
CHALLENGE_TTL = timedelta(minutes=5)
MAX_CODE_ATTEMPTS = 3

settings = {"per_operation_limit_rub": 30_000, "daily_limit_rub": 100_000}

orders: dict[str, Order] = {}
challenges: dict[str, dict] = {}  # challenge_id -> {challenge, order_hash, attempts, used}
payments: dict[str, Payment] = {}


class PolicyViolation(Exception):
    def __init__(self, results: list[PolicyResult]):
        self.results = results
        super().__init__("; ".join(r.detail for r in results if not r.passed))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def order_hash(o: Order) -> str:
    payload = f"{o.order_id}|{o.task_id}|{o.offer_id}|{o.merchant_id}|{o.payee_account}|{o.sku}|{o.amount_rub}"
    return hashlib.sha256(payload.encode()).hexdigest()


def create_order(task: Task, offer_id: str, today: date) -> Order:
    checked = next((co for co in task.offers if co.offer.offer_id == offer_id), None)
    if checked is None:
        raise ValueError("предложение не найдено в результатах поручения")
    if not checked.passed:
        raise ValueError("предложение не прошло проверку условий и не может быть заказано")
    merchant = catalogs.MERCHANTS.get(checked.offer.merchant_id)
    if merchant is None:
        raise ValueError("продавец не найден в реестре банка")
    # Карточку запрашиваем у продавца заново: сумма заказа — актуальная цена, а не та, что видела модель.
    fresh = catalogs.fetch_offer(offer_id, today)
    if fresh is None:
        raise ValueError("продавец больше не предлагает этот товар")
    now = _now()
    o = Order(
        order_id="ord_" + uuid.uuid4().hex[:10], task_id=task.task_id, offer_id=offer_id,
        merchant_id=fresh.merchant_id, merchant_name=merchant["name"], payee_account=merchant["payee_account"],
        sku=fresh.sku, title=fresh.title, amount_rub=fresh.total_rub, status="draft",
        created_at=now, expires_at=now + ORDER_TTL, order_hash="",
    )
    o.order_hash = order_hash(o)
    orders[o.order_id] = o
    audit.log("order-service", "trusted", "order.created", {
        "offer_id": offer_id, "merchant": o.merchant_id, "amount_rub": o.amount_rub,
        "payee_account": o.payee_account, "price_at_search": checked.offer.total_rub,
    }, task_id=task.task_id, order_id=o.order_id)
    return o


def evaluate(o: Order, task: Task, today: date) -> list[PolicyResult]:
    """Policy Engine. Каждое правило независимо; платёж возможен только если прошли все."""
    r: list[PolicyResult] = []
    merchant = catalogs.MERCHANTS.get(o.merchant_id)
    fresh = catalogs.fetch_offer(o.offer_id, today)

    r.append(PolicyResult(rule="order_integrity", passed=hmac.compare_digest(order_hash(o), o.order_hash),
                          detail="заказ не изменялся после создания" if hmac.compare_digest(order_hash(o), o.order_hash)
                          else "данные заказа изменены после создания"))
    r.append(PolicyResult(rule="merchant_allowed", passed=bool(merchant and merchant["allowed"]),
                          detail="продавец в реестре банка" if merchant and merchant["allowed"] else "продавец не допущен"))
    payee_ok = bool(merchant) and o.payee_account == merchant["payee_account"]
    r.append(PolicyResult(rule="payee_match", passed=payee_ok,
                          detail="получатель совпадает с реестром" if payee_ok
                          else f"получатель {o.payee_account} не совпадает с реестром продавца"))
    price_ok = fresh is not None and fresh.total_rub == o.amount_rub
    r.append(PolicyResult(rule="price_recheck", passed=price_ok,
                          detail="цена у продавца не изменилась" if price_ok
                          else f"актуальная цена {_rub(fresh.total_rub) if fresh else '—'} отличается от суммы заказа {_rub(o.amount_rub)}"))
    stock_ok = fresh is not None and fresh.in_stock
    r.append(PolicyResult(rule="stock_recheck", passed=stock_ok, detail="товар в наличии" if stock_ok else "товара нет в наличии"))
    budget = task.constraints.max_total_rub
    budget_ok = budget is None or o.amount_rub <= budget
    r.append(PolicyResult(rule="task_budget", passed=budget_ok,
                          detail="сумма в пределах бюджета поручения" if budget_ok else f"сумма превышает бюджет поручения {_rub(budget)}"))
    limit = settings["per_operation_limit_rub"]
    r.append(PolicyResult(rule="per_operation_limit", passed=o.amount_rub <= limit,
                          detail=f"лимит на операцию {_rub(limit)}" + ("" if o.amount_rub <= limit else " превышен")))
    spent = sum(p.amount_rub for p in payments.values() if p.created_at.date() == _now().date())
    daily_ok = spent + o.amount_rub <= settings["daily_limit_rub"]
    r.append(PolicyResult(rule="daily_limit", passed=daily_ok,
                          detail=f"дневной лимит: потрачено {_rub(spent)} из {_rub(settings['daily_limit_rub'])}" + ("" if daily_ok else ", превышен")))
    r.append(PolicyResult(rule="not_expired", passed=_now() < o.expires_at,
                          detail="заказ действителен" if _now() < o.expires_at else "срок заказа истёк"))
    r.append(PolicyResult(rule="not_paid", passed=o.status != "paid",
                          detail="заказ ещё не оплачен" if o.status != "paid" else "заказ уже оплачен (повтор)"))
    return r


def checkout(o: Order, task: Task, today: date) -> tuple[list[PolicyResult], Challenge | None]:
    results = evaluate(o, task, today)
    failed = [x for x in results if not x.passed]
    audit.log("policy-engine", "trusted", "policy.evaluated", {
        "passed": not failed, "failed_rules": [x.rule for x in failed],
    }, task_id=o.task_id, order_id=o.order_id)
    if failed:
        if o.status != "paid":
            o.status = "rejected"
        return results, None
    ch = Challenge(
        challenge_id="ch_" + uuid.uuid4().hex[:10], order_id=o.order_id, amount_rub=o.amount_rub,
        payee_account=o.payee_account, merchant_name=o.merchant_name, expires_at=_now() + CHALLENGE_TTL,
        sandbox_code=f"{secrets.randbelow(10**6):06d}",
    )
    for e in challenges.values():  # предыдущие запросы подтверждения по этому заказу больше не действуют
        if e["challenge"].order_id == o.order_id:
            e["used"] = True
    challenges[ch.challenge_id] = {"challenge": ch, "order_hash": o.order_hash, "attempts": 0, "used": False}
    o.status = "awaiting_confirmation"
    audit.log("confirmation", "user", "confirmation.requested", {
        "challenge_id": ch.challenge_id, "amount_rub": ch.amount_rub, "payee_account": ch.payee_account,
    }, task_id=o.task_id, order_id=o.order_id)
    return results, ch


def confirm(o: Order, task: Task, challenge_id: str, code: str, today: date) -> Payment:
    entry = challenges.get(challenge_id)
    if entry is None or entry["challenge"].order_id != o.order_id:
        audit.log("confirmation", "user", "confirmation.rejected", {"reason": "unknown_challenge"}, task_id=o.task_id, order_id=o.order_id)
        raise PermissionError("подтверждение не найдено для этого заказа")
    ch: Challenge = entry["challenge"]
    if entry["used"]:
        audit.log("confirmation", "user", "confirmation.rejected", {"reason": "replay"}, task_id=o.task_id, order_id=o.order_id)
        raise PermissionError("подтверждение уже использовано")
    if _now() >= ch.expires_at:
        raise PermissionError("срок подтверждения истёк")
    if entry["order_hash"] != o.order_hash:
        raise PermissionError("заказ изменился после запроса подтверждения")
    entry["attempts"] += 1
    if not hmac.compare_digest(code, ch.sandbox_code):
        audit.log("confirmation", "user", "confirmation.rejected", {"reason": "wrong_code", "attempt": entry["attempts"]},
                  task_id=o.task_id, order_id=o.order_id)
        if entry["attempts"] >= MAX_CODE_ATTEMPTS:
            entry["used"] = True
            o.status = "rejected"
            raise PermissionError("превышено число попыток, заказ отклонён")
        raise PermissionError("неверный код подтверждения")
    entry["used"] = True
    audit.log("user", "user", "confirmation.approved", {"challenge_id": challenge_id}, task_id=o.task_id, order_id=o.order_id)

    # Повторная проверка политик непосредственно перед списанием: цена или наличие могли измениться.
    results = evaluate(o, task, today)
    failed = [x for x in results if not x.passed]
    if failed:
        o.status = "rejected"
        audit.log("policy-engine", "trusted", "policy.blocked_at_payment", {"failed_rules": [x.rule for x in failed]},
                  task_id=o.task_id, order_id=o.order_id)
        raise PolicyViolation(results)
    return _sandbox_pay(o)


def _sandbox_pay(o: Order) -> Payment:
    p = Payment(payment_id="pay_" + uuid.uuid4().hex[:10], order_id=o.order_id, amount_rub=o.amount_rub,
                payee_account=o.payee_account, status="succeeded", created_at=_now())
    payments[p.payment_id] = p
    o.status = "paid"
    audit.log("payment-sandbox", "trusted", "payment.succeeded", {
        "payment_id": p.payment_id, "amount_rub": p.amount_rub, "payee_account": p.payee_account, "sandbox": True,
    }, task_id=o.task_id, order_id=o.order_id)
    return p


def reset() -> None:
    orders.clear()
    challenges.clear()
    payments.clear()
