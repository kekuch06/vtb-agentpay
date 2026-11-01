"""Проверка предложений детерминированным кодом (доверенная зона). Модель здесь не участвует."""
from __future__ import annotations

import re

from ..models import Check, CheckedOffer, Constraints, Offer

CATEGORY_RU = {"ssd": "SSD", "hdd": "HDD", "ram": "оперативная память", "monitor": "монитор", "headphones": "наушники", "gpu": "видеокарта"}

# Признаки попытки управлять агентом через карточку товара или подменить реквизиты.
INJECTION_PATTERNS = [
    r"игнорируй", r"ignore (all |previous )", r"system\s*:", r"инструкци", r"подтверждение не требуется",
    r"переведи", r"\b\d{20}\b", r"реквизит", r"\bassistant\s*:",
]


def listing_is_clean(offer: Offer) -> tuple[bool, str]:
    text = f"{offer.title} {offer.description}".lower()
    hits = [p for p in INJECTION_PATTERNS if re.search(p, text)]
    if hits:
        return False, "в карточке товара найдены инструкции для агента или платёжные реквизиты — предложение исключено"
    return True, "посторонних инструкций и реквизитов нет"


def check_offer(offer: Offer, c: Constraints) -> CheckedOffer:
    checks: list[Check] = []

    ok, detail = listing_is_clean(offer)
    checks.append(Check(name="Целостность карточки", passed=ok, detail=detail))

    if c.category != "other":
        checks.append(Check(
            name="Категория", passed=offer.category == c.category,
            detail=f"{CATEGORY_RU.get(offer.category, offer.category)}, нужно: {CATEGORY_RU.get(c.category, c.category)}",
        ))
    if c.brand:
        checks.append(Check(name="Бренд", passed=offer.brand.lower() == c.brand.lower(), detail=f"{offer.brand}, нужно: {c.brand}"))
    if c.keywords:
        title = offer.title.lower()
        missing = [k for k in c.keywords if k not in title]
        checks.append(Check(
            name="Модель", passed=not missing,
            detail="совпадает" if not missing else f"в названии нет: {', '.join(missing)}",
        ))
    if c.capacity_gb:
        cap = offer.capacity_gb
        ok = cap is not None and abs(cap - c.capacity_gb) <= c.capacity_gb * 0.1
        checks.append(Check(name="Объём", passed=ok, detail=f"{_gb(cap)}, нужно: {_gb(c.capacity_gb)}"))
    if c.max_total_rub:
        checks.append(Check(
            name="Бюджет", passed=offer.total_rub <= c.max_total_rub,
            detail=f"{_rub(offer.total_rub)} с доставкой, лимит {_rub(c.max_total_rub)}",
        ))
    if c.deliver_by:
        checks.append(Check(
            name="Срок доставки", passed=offer.delivery_date <= c.deliver_by,
            detail=f"{offer.delivery_date:%d.%m}, нужно не позже {c.deliver_by:%d.%m}",
        ))
    if c.min_warranty_months is not None:
        checks.append(Check(
            name="Гарантия", passed=offer.warranty_months >= c.min_warranty_months,
            detail=f"{offer.warranty_months} мес., нужно от {c.min_warranty_months} мес.",
        ))
    checks.append(Check(name="Наличие", passed=offer.in_stock, detail="в наличии" if offer.in_stock else "нет в наличии"))

    passed = all(ch.passed for ch in checks)
    # Ранжирование по итоговой цене с доставкой; при равенстве оркестратор берёт более раннюю доставку.
    return CheckedOffer(offer=offer, checks=checks, passed=passed, score=offer.total_rub if passed else None)


def _rub(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " ₽"


def _gb(v: int | None) -> str:
    if v is None:
        return "не указан"
    return f"{v / 1000:g} ТБ" if v >= 1000 else f"{v} ГБ"
