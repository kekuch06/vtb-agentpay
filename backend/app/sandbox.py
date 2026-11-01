"""Сценарии sandbox: события у продавца и атаки, которые должен перехватить платёжный контур. Только для стенда.

Сценарий действует на клиента, который его запустил, и хранится в базе: на общем стенде сценарий одного эксперта
не ломает заказ другому, а при нескольких процессах uvicorn срабатывает во всех."""
from __future__ import annotations

from datetime import timedelta

from . import audit, catalogs, db, store
from .trust import payments


def run(owner: str, name: str, offer_id: str, order_id: str) -> str:
    """Сообщение для интерфейса. LookupError — нет заказа (404), ValueError — сценарий неприменим (409),
    KeyError — неизвестный сценарий или предложение (400)."""
    if name == "reset":
        store.clear_sandbox(owner)
        audit.log("sandbox", "untrusted", "sandbox.reset", {}, owner=owner)
        return "каталоги восстановлены"
    if name == "payee_swap":
        with db.tx():
            o = _order(owner, order_id)
            o.payee_account = "40817810000000666666"  # имитация подмены реквизитов после создания заказа
            store.save_order(owner, o)
            audit.log("sandbox", "untrusted", "sandbox.payee_swapped", {"payee_account": o.payee_account},
                      task_id=o.task_id, order_id=o.order_id, owner=owner)
        return "реквизиты получателя в заказе подменены"
    if name == "code_expired":
        with db.tx():
            o = _order(owner, order_id)
            if not store.expire_challenges(o.order_id, payments._now() - timedelta(seconds=1)):
                raise ValueError("у заказа нет действующего кода подтверждения")
            audit.log("sandbox", "untrusted", "sandbox.code_expired", {}, task_id=o.task_id, order_id=o.order_id, owner=owner)
        return "срок кода подтверждения истёк"
    message = catalogs.check_scenario(name, offer_id)
    with db.tx():
        store.add_sandbox_event(owner, name, offer_id)
        audit.log("sandbox", "untrusted", f"sandbox.{name}", {"offer_id": offer_id}, owner=owner)
    return message


def _order(owner: str, order_id: str):
    o = store.get_order(owner, order_id)
    if o is None:
        raise LookupError("заказ не найден")
    return o
