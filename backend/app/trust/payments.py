"""Доверенная зона: сервис заказов, Policy Engine, подтверждение пользователем и платёжный sandbox.

Ни одна функция здесь не принимает текст модели. Заказ собирается из повторно запрошенной карточки
продавца и реквизитов из доверенного реестра; платёж исполняется только по подтверждённому челленджу.
Каждая операция — одна транзакция хранилища (db.tx): заказ перечитывается внутри неё, поэтому проверки и
изменения видят согласованное состояние даже при параллельных запросах.
"""
from __future__ import annotations

import hmac
import os
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import audit, catalogs, db, keys, store
from ..models import Challenge, DeviceMessage, Order, Payment, PolicyResult, Task
from .verifier import check_offer, money, rub

ORDER_TTL = timedelta(minutes=15)
CHALLENGE_TTL = timedelta(minutes=5)
MAX_CODE_ATTEMPTS = 3  # неверных кодов на заказ, суммарно по всем выданным кодам
MAX_CHALLENGES = 3  # запросов кода на заказ («не пришёл код»)
TERMINAL = ("paid", "rejected", "expired")  # из этих статусов заказ не возвращается
BANK_TZ = ZoneInfo(os.getenv("AGENTPAY_TZ", "Europe/Moscow"))

settings = {"per_operation_limit_rub": 30_000, "daily_limit_rub": 100_000}  # значения по умолчанию для нового клиента


def settings_for(owner: str) -> dict:
    return {**settings, **store.get_settings(owner)}


def _set_limit(owner: str, value: int) -> None:
    store.put_settings(owner, {**store.get_settings(owner), "per_operation_limit_rub": value})
    audit.log("user", "user", "settings.changed", {"per_operation_limit_rub": value}, owner=owner)


def change_limit(owner: str, value: int) -> dict | None:
    """Понижение лимита на одну покупку — сразу. Повышение — только кодом с устройства клиента: иначе тот, кто
    получил доступ к сессии (скрипт, агент с API), сам расширял бы рамки, в которых платежи проходят Policy Engine.
    Возвращает запрос подтверждения или None, если лимит уже изменён."""
    with db.tx():
        current = settings_for(owner)["per_operation_limit_rub"]
        if value <= current:
            _set_limit(owner, value)
            return None
        request_id = "lim_" + uuid.uuid4().hex[:10]
        code = f"{secrets.randbelow(10**6):06d}"
        expires = _now() + CHALLENGE_TTL
        store.save_limit_request(owner, request_id, {"value": value, "from": current, "attempts": 0, "expires_at": expires.isoformat(),
                                                     "code_mac": keys.mac("limit-otp", request_id, owner, str(value), code)})
        store.push_to_device(owner, DeviceMessage(kind="limit", challenge_id=request_id, order_id="", merchant_name="Повышение лимита",
                                                  amount_kop=value * 100, code=code, expires_at=expires))
        audit.log("confirmation", "user", "settings.raise_requested", {"from": current, "to": value, "request_id": request_id}, owner=owner)
        return {"request_id": request_id, "value": value, "from": current, "expires_at": expires.isoformat()}


def confirm_limit(owner: str, request_id: str, code: str) -> None:
    """Подтверждение повышения: одноразовый запрос, не больше MAX_CODE_ATTEMPTS неверных кодов, срок как у кода оплаты.
    Отказ фиксируется в журнале и счётчике попыток до исключения, как и у подтверждения платежа."""
    with db.tx():
        outcome = _confirm_limit(owner, request_id, code)
    if outcome is not None:
        raise outcome


def _confirm_limit(owner: str, request_id: str, code: str) -> Exception | None:
    found = store.get_limit_request(owner, request_id)
    if found is None:
        return LookupError("запрос на изменение лимита не найден")
    req, used = found

    def reject(reason: str, message: str) -> PermissionError:
        audit.log("confirmation", "user", "settings.raise_rejected", {"reason": reason, "request_id": request_id}, owner=owner)
        return PermissionError(message)
    if used:
        return reject("closed", "этот код больше не действует, запросите изменение лимита заново")
    if _now() >= datetime.fromisoformat(req["expires_at"]):
        store.update_limit_request(request_id, req, used=True)
        return reject("expired", "срок кода истёк, запросите изменение лимита заново")
    if not hmac.compare_digest(keys.mac("limit-otp", request_id, owner, str(req["value"]), code), req["code_mac"]):
        req["attempts"] += 1
        left = MAX_CODE_ATTEMPTS - req["attempts"]
        store.update_limit_request(request_id, req, used=left <= 0)
        return reject("wrong_code", "неверный код, попытки закончились — лимит не изменён" if left <= 0
                      else f"неверный код, осталось попыток: {left}")
    store.update_limit_request(request_id, req, used=True)
    _set_limit(owner, req["value"])
    return None


class PolicyViolation(Exception):
    def __init__(self, results: list[PolicyResult]):
        self.results = results
        super().__init__("; ".join(r.detail for r in results if not r.passed))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def bank_day(dt: datetime) -> date:
    """Операционный день банка: дневной лимит обнуляется в полночь по часовому поясу банка, а не по UTC."""
    return dt.astimezone(BANK_TZ).date()


def order_hash(o: Order) -> str:
    """HMAC полей заказа, которые определяют платёж. Без ключа сервера подменить заказ в базе и пересчитать хеш нельзя."""
    return keys.mac("order", o.order_id, o.task_id, o.offer_id, o.merchant_id, o.payee_account, o.sku, str(o.amount_kop))


def create_order(owner: str, task: Task, offer_id: str, today: date) -> Order:
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
        sku=fresh.sku, title=fresh.title, amount_kop=fresh.total_kop, status="draft",
        created_at=now, expires_at=now + ORDER_TTL, order_hash="",
    )
    o.order_hash = order_hash(o)
    with db.tx():
        store.save_order(owner, o)
        audit.log("order-service", "trusted", "order.created", {
            "offer_id": offer_id, "merchant": o.merchant_id, "amount_kop": o.amount_kop,
            "payee_account": o.payee_account, "price_at_search_kop": checked.offer.total_kop,
        }, task_id=task.task_id, order_id=o.order_id, owner=owner)
    return o


def evaluate(owner: str, o: Order, task: Task, today: date) -> list[PolicyResult]:
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
    price_ok = fresh is not None and fresh.total_kop == o.amount_kop
    r.append(PolicyResult(rule="price_recheck", passed=price_ok,
                          detail="цена у продавца не изменилась" if price_ok
                          else f"актуальная цена {money(fresh.total_kop) if fresh else '—'} отличается от суммы заказа {money(o.amount_kop)}"))
    stock_ok = fresh is not None and fresh.in_stock
    r.append(PolicyResult(rule="stock_recheck", passed=stock_ok, detail="товар в наличии" if stock_ok else "товара нет в наличии"))
    # Условия поручения проверяются заново по актуальной карточке: продавец мог снять гарантию, сдвинуть доставку
    # или дописать в описание инструкции агенту уже после того, как предложение прошло проверку.
    rechecked = check_offer(fresh, task.constraints) if fresh is not None else None
    lost = [c.name.lower() for c in rechecked.checks if not c.passed] if rechecked else ["карточка недоступна"]
    r.append(PolicyResult(rule="offer_recheck", passed=not lost,
                          detail="карточка продавца по-прежнему выполняет условия поручения" if not lost
                          else f"карточка продавца изменилась, условия не выполняются: {', '.join(lost)}"))
    # Поручение — одна покупка: второй заказ по нему (двойной клик, другой продавец) уже не оплачивается,
    # иначе два заказа по поручению «до 16 тысяч» в сумме выходили за бюджет клиента.
    paid_by_task = store.payments_for_task(o.task_id)
    r.append(PolicyResult(rule="task_open", passed=not paid_by_task,
                          detail="по поручению ещё нет оплаты" if not paid_by_task
                          else f"по поручению уже оплачен заказ {paid_by_task[0].order_id}"))
    budget = task.constraints.max_total_rub
    budget_ok = budget is None or o.amount_kop <= budget * 100
    r.append(PolicyResult(rule="task_budget", passed=budget_ok,
                          detail="сумма в пределах бюджета поручения" if budget_ok else f"сумма превышает бюджет поручения {rub(budget)}"))
    limits = settings_for(owner)
    limit = limits["per_operation_limit_rub"]
    limit_ok = o.amount_kop <= limit * 100
    r.append(PolicyResult(rule="per_operation_limit", passed=limit_ok,
                          detail=f"лимит на одну покупку {rub(limit)}" + ("" if limit_ok else " превышен")))
    spent = store.spent_on(owner, bank_day(_now()))
    daily_ok = spent + o.amount_kop <= limits["daily_limit_rub"] * 100
    r.append(PolicyResult(rule="daily_limit", passed=daily_ok,
                          detail=f"дневной лимит: потрачено {money(spent)} из {rub(limits['daily_limit_rub'])}" + ("" if daily_ok else ", превышен")))
    r.append(PolicyResult(rule="not_expired", passed=_now() < o.expires_at,
                          detail="заказ действителен" if _now() < o.expires_at else "срок заказа истёк"))
    r.append(PolicyResult(rule="not_paid", passed=o.status != "paid",
                          detail="заказ ещё не оплачен" if o.status != "paid" else "заказ уже оплачен (повтор)"))
    closed = {"rejected": "заказ отклонён, оформите новый", "expired": "заказ истёк, оформите новый", "paid": "заказ закрыт"}
    r.append(PolicyResult(rule="order_open", passed=o.status not in TERMINAL,
                          detail="заказ открыт" if o.status not in TERMINAL else closed[o.status]))
    return r


def _close(o: Order, results: list[PolicyResult]) -> None:
    """Отказ Policy Engine закрывает заказ: «оживить» его повторным checkout нельзя."""
    if o.status in TERMINAL:
        return
    failed = {x.rule for x in results if not x.passed}
    o.status = "expired" if failed == {"not_expired"} else "rejected"


def _reject_confirmation(owner: str, o: Order, reason: str, message: str, **data) -> PermissionError:
    audit.log("confirmation", "user", "confirmation.rejected", {"reason": reason, **data}, task_id=o.task_id, order_id=o.order_id, owner=owner)
    return PermissionError(message)


def _load(owner: str, order_id: str) -> tuple[Order, Task]:
    o = store.get_order(owner, order_id)
    task = store.get_task(owner, o.task_id) if o else None
    if o is None or task is None:
        raise LookupError("заказ не найден")
    return o, task


def checkout(owner: str, order_id: str, today: date) -> tuple[Order, list[PolicyResult], Challenge | None]:
    with db.tx():
        o, task = _load(owner, order_id)
        results = evaluate(owner, o, task, today)
        if not any(not x.passed for x in results) and o.challenges_issued >= MAX_CHALLENGES:
            results.append(PolicyResult(rule="confirmation_limit", passed=False,
                                        detail=f"код подтверждения запрошен {MAX_CHALLENGES} раза, заказ отклонён"))
        failed = [x for x in results if not x.passed]
        audit.log("policy-engine", "trusted", "policy.evaluated", {
            "passed": not failed, "failed_rules": [x.rule for x in failed],
        }, task_id=o.task_id, order_id=o.order_id, owner=owner)
        if failed:
            _close(o, results)
            store.save_order(owner, o)
            return o, results, None
        ch = Challenge(
            challenge_id="ch_" + uuid.uuid4().hex[:10], order_id=o.order_id, amount_kop=o.amount_kop,
            payee_account=o.payee_account, merchant_name=o.merchant_name, expires_at=_now() + CHALLENGE_TTL,
        )
        code = f"{secrets.randbelow(10**6):06d}"
        # Код не возвращается вызвавшему checkout: он уходит отдельным каналом на устройство клиента,
        # а в хранилище остаётся только HMAC, привязанный к челленджу.
        store.save_challenge(ch, o.order_hash, keys.mac("otp", ch.challenge_id, code))  # прежние запросы по заказу аннулируются
        store.push_to_device(owner, DeviceMessage(challenge_id=ch.challenge_id, order_id=o.order_id, merchant_name=o.merchant_name,
                                                  amount_kop=o.amount_kop, code=code, expires_at=ch.expires_at))
        o.challenges_issued += 1
        o.status = "awaiting_confirmation"
        store.save_order(owner, o)
        audit.log("confirmation", "user", "confirmation.requested", {
            "challenge_id": ch.challenge_id, "amount_kop": ch.amount_kop, "payee_account": ch.payee_account,
            "issued": o.challenges_issued,
        }, task_id=o.task_id, order_id=o.order_id, owner=owner)
        return o, results, ch


def confirm(owner: str, order_id: str, challenge_id: str, code: str, today: date) -> tuple[Order, Payment]:
    # Отказ (неверный код, истёкший срок) тоже меняет состояние — счётчик попыток и журнал — и должен быть
    # зафиксирован, поэтому исключение возбуждается после выхода из транзакции, а не откатывает её.
    with db.tx():
        o, task = _load(owner, order_id)
        outcome = _confirm(owner, o, task, challenge_id, code, today)
        store.save_order(owner, o)
    if isinstance(outcome, Exception):
        raise outcome
    return o, outcome


def _confirm(owner: str, o: Order, task: Task, challenge_id: str, code: str, today: date) -> Payment | Exception:
    found = store.get_challenge(challenge_id)
    if found is None or found[0].order_id != o.order_id:
        return _reject_confirmation(owner, o, "unknown_challenge", "подтверждение не найдено для этого заказа")
    ch, hash_at_request, code_mac, used = found
    if used:
        return _reject_confirmation(owner, o, "replay", "этот код больше не действует, запросите новый")
    if o.status != "awaiting_confirmation" or o.code_attempts >= MAX_CODE_ATTEMPTS:
        return _reject_confirmation(owner, o, "order_closed", "заказ закрыт, оформите новый")
    if _now() >= ch.expires_at:
        store.use_challenge(challenge_id)
        return _reject_confirmation(owner, o, "expired", "срок подтверждения истёк, запросите новый код")
    if not hmac.compare_digest(hash_at_request, order_hash(o)):
        store.use_challenge(challenge_id)
        return _reject_confirmation(owner, o, "order_changed", "заказ изменился после отправки кода, запросите новый код")
    if not hmac.compare_digest(keys.mac("otp", challenge_id, code), code_mac):
        o.code_attempts += 1
        left = MAX_CODE_ATTEMPTS - o.code_attempts
        if left <= 0:
            store.use_challenge(challenge_id)
            o.status = "rejected"
            return _reject_confirmation(owner, o, "wrong_code", "неверный код, попытки закончились — заказ отклонён", attempt=o.code_attempts)
        return _reject_confirmation(owner, o, "wrong_code", f"неверный код, осталось попыток: {left}", attempt=o.code_attempts)
    store.use_challenge(challenge_id)
    audit.log("user", "user", "confirmation.approved", {"challenge_id": challenge_id}, task_id=o.task_id, order_id=o.order_id, owner=owner)

    # Повторная проверка политик непосредственно перед списанием: цена или наличие могли измениться.
    results = evaluate(owner, o, task, today)
    failed = [x for x in results if not x.passed]
    if failed:
        _close(o, results)
        audit.log("policy-engine", "trusted", "policy.blocked_at_payment", {"failed_rules": [x.rule for x in failed]},
                  task_id=o.task_id, order_id=o.order_id, owner=owner)
        return PolicyViolation(results)
    return _sandbox_pay(owner, o)


def _sandbox_pay(owner: str, o: Order) -> Payment:
    p = Payment(payment_id="pay_" + uuid.uuid4().hex[:10], order_id=o.order_id, task_id=o.task_id, amount_kop=o.amount_kop,
                payee_account=o.payee_account, status="succeeded", created_at=_now())
    store.insert_payment(owner, p, bank_day(p.created_at))
    o.status = "paid"
    audit.log("payment-sandbox", "trusted", "payment.succeeded", {
        "payment_id": p.payment_id, "amount_kop": p.amount_kop, "payee_account": p.payee_account, "sandbox": True,
    }, task_id=o.task_id, order_id=o.order_id, owner=owner)
    return p


def all_payments() -> list[Payment]:
    return store.all_payments()


def reset() -> None:
    store.clear()
