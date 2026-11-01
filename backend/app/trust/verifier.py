"""Проверка предложений детерминированным кодом (доверенная зона). Модель здесь не участвует."""
from __future__ import annotations

import re
import unicodedata

from ..models import Check, CheckedOffer, Constraints, Offer

CATEGORY_RU = {"ssd": "SSD", "hdd": "HDD", "ram": "оперативная память", "monitor": "монитор", "headphones": "наушники", "gpu": "видеокарта"}

# Признаки попытки управлять агентом через карточку товара или подменить реквизиты.
INJECTION_PATTERNS = [
    r"игнорируй", r"забудь", r"не учитывай", r"ignore (all |previous )", r"disregard", r"system\s*:", r"\bassistant\s*:",
    r"(предыдущ|прежн|прошл|все|системн)\w*\s+(инструкц|указани)", r"инструкци\w*\s+(для\s+)?(агент|ассистент|модел|бот)",
    r"подтверждение не требуется", r"переведи", r"реквизит",
]

# Обфускация, которой проверку обходили бы без нормализации: невидимые символы внутри слова,
# латиница вместо похожей кириллицы (и наоборот), разделители внутри номера счёта.
INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"), None)
LAT_TO_CYR = str.maketrans("aeopcyxkmthb", "аеорсухкмтнв")
CYR_TO_LAT = str.maketrans("аеорсухкмтнв", "aeopcyxkmthb")


SEP = r"[\s\-\u2010-\u2015./_]+"


def _plain(offer: Offer) -> str:
    t = unicodedata.normalize("NFKC", f"{offer.title} {offer.brand} {offer.sku} {offer.description}")
    return t.translate(INVISIBLE).lower().replace("ё", "е")


def _normalized(offer: Offer) -> tuple[str, str]:
    t = re.sub(rf"(?<=\d){SEP}(?=\d)", "", _plain(offer))  # «4081 7810 …», «40817-810-…» → один номер
    return t.translate(LAT_TO_CYR), t.translate(CYR_TO_LAT)


def has_account_number(text: str) -> bool:
    """Номер счёта — 20 цифр: подряд или группами через пробел, дефис, точку («4081 7810 0000 0066 6666»).
    Ищутся подряд идущие группы, дающие ровно 20 цифр, поэтому примыкающая цифра или буква («Артикул 7 4081…»,
    «счёт4081…») номер не прячет, а длинный артикул из 25 цифр за счёт не принимается."""
    for run in re.finditer(rf"\d+(?:{SEP}\d+)*", text):
        sizes = [len(g) for g in re.findall(r"\d+", run.group())]
        for i in range(len(sizes)):
            total = 0
            for size in sizes[i:]:
                total += size
                if total >= 20:
                    if total == 20:
                        return True
                    break
    return False


def listing_is_clean(offer: Offer) -> tuple[bool, str]:
    variants = _normalized(offer)
    hits = [p for p in INJECTION_PATTERNS if any(re.search(p, v) for v in variants)]
    if hits or has_account_number(_plain(offer)):
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
            name="Бюджет", passed=offer.total_kop <= c.max_total_rub * 100,
            detail=f"{_money(offer.total_kop)} с доставкой, лимит {_rub(c.max_total_rub)}",
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
    return CheckedOffer(offer=offer, checks=checks, passed=passed, score=offer.total_kop if passed else None)


def _rub(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " ₽"


def _money(kop: int) -> str:
    """Сумма в копейках для человека: 1848750 → «18 487,50 ₽», 1479000 → «14 790 ₽»."""
    rub, k = divmod(kop, 100)
    return _rub(rub) if not k else f"{rub:,}".replace(",", " ") + f",{k:02d} ₽"


def _gb(v: int | None) -> str:
    if v is None:
        return "не указан"
    return f"{v / 1000:g} ТБ" if v >= 1000 else f"{v} ГБ"
