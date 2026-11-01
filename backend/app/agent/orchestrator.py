"""Оркестратор агента: конечный автомат «разбор → поиск по каталогам → проверка → выбор → объяснение».

Оркестратор не имеет доступа к сервису заказов и платежам: результат его работы — только
список проверенных предложений и рекомендация. Заказ создаёт пользователь.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime, timezone

from .. import audit, catalogs
from ..models import Constraints, Task, TraceStep
from ..trust.verifier import CATEGORY_RU, check_offer, gb, money, rub
from . import grounding, llm, rules


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def extract_constraints(text: str, today: date) -> tuple[Constraints, str, str]:
    """Возвращает (ограничения, источник, пояснение). LLM — если доступна, иначе правила.

    По умолчанию ответ модели сверяется с текстом поручения (grounding); AGENTPAY_GROUNDING=off
    отключает сверку — нужно только для сравнения в прогоне метрик."""
    if llm.available():
        try:
            llm_c = llm.extract(text, today)
            if os.getenv("AGENTPAY_GROUNDING", "on") == "off":
                return llm_c, f"llm:{llm.MODEL}", "ответ модели прошёл проверку схемы"
            merged, fixes = grounding.merge(llm_c, rules.parse(text, today), text)
            note = "ответ модели прошёл проверку схемы и сверку с текстом"
            if fixes:
                note += f"; исправлено кодом: {', '.join(fixes)}"
            return merged, f"llm+grounding:{llm.MODEL}", note
        except llm.LLMUnavailable as e:
            return rules.parse(text, today), "rules", f"модель недоступна или ответ отклонён ({str(e)[:120]}), использован разбор правилами"
    return rules.parse(text, today), "rules", "локальная модель не запущена, использован разбор правилами"


def describe(c: Constraints) -> str:
    parts = []
    if c.category != "other":
        parts.append(CATEGORY_RU.get(c.category, c.category))
    if c.brand:
        parts.append(c.brand)
    if c.keywords:
        parts.append(" ".join(c.keywords))
    if c.capacity_gb:
        parts.append(gb(c.capacity_gb))
    if c.max_total_rub:
        parts.append(f"до {rub(c.max_total_rub)}")
    if c.deliver_by:
        parts.append(f"доставка до {c.deliver_by:%d.%m}")
    if c.min_warranty_months:
        parts.append("с гарантией" if c.min_warranty_months == 1 else f"гарантия от {c.min_warranty_months} мес.")
    return ", ".join(parts) or "условия не распознаны"


def run(text: str, today: date | None = None, owner: str | None = None) -> Task:
    today = today or date.today()
    task_id = "task_" + uuid.uuid4().hex[:10]
    trace: list[TraceStep] = []
    audit.log("user", "user", "task.created", {"text": text}, task_id=task_id, owner=owner)

    t0 = time.perf_counter()
    constraints, parser, note = extract_constraints(text, today)
    trace.append(TraceStep(step="Разбор поручения", zone="untrusted", detail=f"{describe(constraints)} · {note}", ms=_ms(t0)))
    audit.log("agent", "untrusted", "constraints.extracted", {"parser": parser, "constraints": constraints.model_dump(mode="json")}, task_id=task_id, owner=owner)

    # Товар не назван — ни категории, ни бренда, ни модели: «любой товар» не подставляется. Иначе на «видеокарту RTX»
    # или «что-нибудь до 10 тысяч» агент рекомендовал самый дешёвый товар каталога, например SSD.
    if constraints.category == "other" and not constraints.brand and not constraints.keywords:
        explanation = ("Не понял, какой товар нужен: в поручении нет категории, бренда или модели из подключённых каталогов. "
                       "Уточните, например: «SSD Samsung 2 ТБ до 16 тысяч».")
        trace.append(TraceStep(step="Рекомендация", zone="trusted", detail="товар не распознан, поиск по каталогам не выполнялся"))
        audit.log("agent", "trusted", "recommendation.made", {"offer_id": None, "reason": "product_not_recognized"}, task_id=task_id, owner=owner)
        return Task(task_id=task_id, text=text, created_at=datetime.now(timezone.utc), parser=parser, constraints=constraints,
                    offers=[], recommended_offer_id=None, explanation=explanation, trace=trace)

    checked = []
    for merchant_id in catalogs.ADAPTERS:
        t0 = time.perf_counter()
        offers, broken = catalogs.scan(merchant_id, today)
        relevant = [o for o in offers if constraints.category in ("other", o.category)]
        trace.append(TraceStep(step=f"Каталог «{catalogs.MERCHANTS[merchant_id]['name']}»", zone="untrusted",
                               detail=f"получено {len(offers)} карточек, по категории {len(relevant)}"
                                      + (f", отброшено некорректных {broken}" if broken else ""), ms=_ms(t0)))
        audit.log("agent", "untrusted", "catalog.searched", {"merchant": merchant_id, "offers": len(offers), "relevant": len(relevant),
                                                             "broken": broken}, task_id=task_id, owner=owner)
        checked += [check_offer(o, constraints) for o in relevant]

    t0 = time.perf_counter()
    passed = sorted((c for c in checked if c.passed), key=lambda c: (c.score, c.offer.delivery_date))
    rejected = [c for c in checked if not c.passed]
    injected = [c for c in rejected if not c.checks[0].passed]
    trace.append(TraceStep(step="Проверка условий кодом", zone="trusted",
                           detail=f"{len(checked)} предложений: прошли {len(passed)}, отклонены {len(rejected)}"
                                  + (f", из них {len(injected)} с подозрительной карточкой" if injected else ""), ms=_ms(t0)))
    audit.log("verifier", "trusted", "offers.verified", {
        "total": len(checked), "passed": [c.offer.offer_id for c in passed],
        "suspicious": [c.offer.offer_id for c in injected],
    }, task_id=task_id, owner=owner)

    best = passed[0] if passed else None
    explanation = _explain(constraints, checked, passed, injected)
    trace.append(TraceStep(step="Рекомендация", zone="trusted",
                           detail=f"{best.offer.title} · {best.offer.merchant_name}" if best else "подходящих предложений нет"))
    audit.log("agent", "trusted", "recommendation.made", {"offer_id": best.offer.offer_id if best else None}, task_id=task_id, owner=owner)

    # Порядок для интерфейса: прошедшие по рейтингу, затем отклонённые.
    ordered = passed + sorted(rejected, key=lambda c: c.offer.total_kop)
    return Task(task_id=task_id, text=text, created_at=datetime.now(timezone.utc), parser=parser, constraints=constraints,
                offers=ordered, recommended_offer_id=best.offer.offer_id if best else None, explanation=explanation, trace=trace)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def _offers(n: int) -> str:
    return _plural(n, "предложение", "предложения", "предложений")


def _sellers(n: int) -> str:
    return _plural(n, "продавца", "продавцов", "продавцов")


def _explain(c: Constraints, checked, passed, injected) -> str:
    # Объяснение собирается шаблоном из проверенных полей, а не генерируется моделью:
    # текст карточек продавцов не может попасть в ответ пользователю как «мнение агента».
    merchants = len({x.offer.merchant_id for x in checked})
    if not checked:
        return "Не нашёл товаров этой категории в подключённых каталогах."
    if not passed:
        fails: dict[str, int] = {}
        for x in checked:
            for ch in x.checks:
                if not ch.passed:
                    fails[ch.name] = fails.get(ch.name, 0) + 1
        top = ", ".join(f"{k.lower()} ({v})" for k, v in sorted(fails.items(), key=lambda kv: -kv[1])[:3])
        return (f"Проверил {_offers(len(checked))} у {_sellers(merchants)} — ни одно не выполняет все условия. "
                f"Чаще всего не проходят: {top}. Попробуйте ослабить условия.")
    b = passed[0].offer
    s = (f"Проверил {_offers(len(checked))} у {_sellers(merchants)}, условиям {'соответствует' if len(passed) == 1 else 'соответствуют'} {len(passed)}. "
         f"Лучший вариант — {b.title} у продавца «{b.merchant_name}»: {money(b.total_kop)} с доставкой, "
         f"привезут {b.delivery_date:%d.%m}, гарантия {b.warranty_months} мес.")
    if len(passed) > 1:
        diff = passed[1].offer.total_kop - b.total_kop
        if diff > 0:
            s += f" Это на {money(diff)} дешевле следующего подходящего варианта."
    if injected:
        s += f" Исключено подозрительных карточек: {len(injected)} — в карточке были инструкции для агента или чужие реквизиты."
    return s
