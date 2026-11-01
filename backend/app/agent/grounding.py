"""Проверка ответа модели по тексту поручения.

Модель хорошо понимает свободные формулировки, но может придумать поле, которого нет в тексте,
или ошибиться в календарной арифметике. Поэтому каждое поле из ответа модели принимается, только если
оно опирается на текст поручения; даты и единицы измерения считает код. Если детерминированный разбор
нашёл поле сам, побеждает он.
"""
from __future__ import annotations

import re

from ..models import Constraints
from . import rules

WORD_NUMBERS = {"один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4, "пять": 5}
# Характеристики, а не модели: по ним нельзя требовать совпадения в названии товара.
SPEC_TERMS = {"ssd", "hdd", "nvme", "sata", "pcie", "m.2", "ddr4", "ddr5", "usb", "tb", "gb", "тб", "гб"}
DEADLINE_CUES = r"\bдо\b|\bк\s|\bза\s|завтра|недел|\bдн|дня|числа|срок"


def _norm(text: str) -> str:
    t = text.lower().replace("ё", "е")
    for alias, canon in rules.ALIASES.items():
        t = t.replace(alias, canon)
    return t


def _numbers(t: str) -> set[float]:
    nums = {float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", t))}
    nums |= {float(v) for w, v in WORD_NUMBERS.items() if re.search(rf"\b{w}\b", t)}
    return nums


def _grounded_number(value: int, t: str, scales: tuple[int, ...]) -> bool:
    nums = _numbers(t)
    return any(abs(value / s - n) < 1e-6 for s in scales for n in nums)


def merge(llm_c: Constraints, rules_c: Constraints, text: str) -> tuple[Constraints, list[str]]:
    t = _norm(text)
    fixes: list[str] = []
    out = llm_c.model_copy()

    if rules_c.category != "other" and llm_c.category != rules_c.category:
        out.category = rules_c.category
        fixes.append("категория")

    if llm_c.brand and not re.search(rules.BRANDS.get(llm_c.brand, re.escape(llm_c.brand.lower())), t):
        out.brand = rules_c.brand
        fixes.append(f"бренд «{llm_c.brand}» не найден в тексте")
    elif not llm_c.brand and rules_c.brand:
        out.brand = rules_c.brand

    grounded_kw = [k for k in llm_c.keywords if k.lower() in t and k.lower() not in SPEC_TERMS
                   and not re.fullmatch(r"\d+\s*(tb|gb|тб|гб)", k.lower())]
    if len(grounded_kw) != len(llm_c.keywords):
        fixes.append("отброшены модели товаров, которых нет в тексте")
    kws = list(dict.fromkeys(grounded_kw + rules_c.keywords))
    if "wh-1000xm5" in kws and "xm5" in kws:
        kws.remove("xm5")
    out.keywords = kws

    if rules_c.capacity_gb:
        out.capacity_gb = rules_c.capacity_gb
    elif llm_c.capacity_gb and not _grounded_number(llm_c.capacity_gb, t, (1, 1000, 1024)):
        out.capacity_gb = None
        fixes.append("объём не найден в тексте")

    if rules_c.max_total_rub:
        out.max_total_rub = rules_c.max_total_rub
    elif llm_c.max_total_rub and not _grounded_number(llm_c.max_total_rub, t, (1, 1000)):
        out.max_total_rub = None
        fixes.append("бюджет не найден в тексте")

    # Календарную арифметику («до пятницы» → дата) считает код.
    if rules_c.deliver_by:
        if llm_c.deliver_by != rules_c.deliver_by:
            fixes.append("срок пересчитан кодом")
        out.deliver_by = rules_c.deliver_by
    elif llm_c.deliver_by and not re.search(DEADLINE_CUES, t):
        out.deliver_by = None
        fixes.append("срок не упоминается в тексте")

    if rules_c.min_warranty_months is not None:
        out.min_warranty_months = rules_c.min_warranty_months
    elif llm_c.min_warranty_months is not None and "гарант" not in t:
        out.min_warranty_months = None
        fixes.append("гарантия не упоминается в тексте")

    if out.category in ("ssd", "hdd", "ram", "other") or out.capacity_gb is None:
        pass
    else:
        out.capacity_gb = None
    return Constraints.model_validate(out.model_dump()), fixes
