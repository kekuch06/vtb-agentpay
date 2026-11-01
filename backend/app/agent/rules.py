"""Детерминированный разбор поручения. Работает без LLM и служит запасным вариантом, если модель недоступна
или вернула невалидный ответ."""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..models import Constraints

CATEGORIES = [
    ("ssd", r"\bssd\b|ссд|твердотельн|накопител"),
    ("hdd", r"\bhdd\b|жёстк|жестк|винчестер"),
    ("ram", r"оперативк|оперативн|\bозу\b|\bram\b|ddr\d"),
    ("monitor", r"монитор"),
    ("headphones", r"наушник"),
]

BRANDS = {
    "Samsung": r"samsung|самсунг",
    "Kingston": r"kingston|кингстон",
    "Crucial": r"crucial|кручиал",
    "Western Digital": r"western digital|\bwd\b",
    "Seagate": r"seagate|сигейт",
    "Sony": r"\bsony\b|сони",
}

MODEL_CATEGORY = {
    "990 evo": "ssd", "990 pro": "ssd", "870 evo": "ssd", "kc3000": "ssd", "sn850x": "ssd", "p3": "ssd",
    "wh-1000xm5": "headphones", "xm5": "headphones", "fury beast": "ram", "odyssey g5": "monitor", "barracuda": "hdd",
}
MODELS = list(MODEL_CATEGORY)
ALIASES = {"990 про": "990 pro", "990 эво": "990 evo", "870 эво": "870 evo"}

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6,
}

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def parse_budget(t: str) -> int | None:
    for m in re.finditer(
        r"(?:до|не дороже|дешевле|максимум|в пределах|бюджет(?:ом)?|не больше|за)\s*"
        r"(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*(тысяч\w*|тыс\.?|т\.р\.?|к\b|k\b|руб\w*|р\b|₽)?",
        t,
    ):
        unit = (m.group(2) or "").lower()
        # «до 5 дней», «до 2 ТБ», «до 5 ноября» — не бюджет
        tail = t[m.end(1):m.end(1) + 12]
        if not unit and re.match(r"\s*(дн|день|тб|tb|гб|gb|лет|год|мес|числа|ч\b|час|[а-я]+бр|\.)", tail):
            continue
        if not unit and re.fullmatch(r"\d{1,2}[.,]\d{2}", m.group(1)):  # «до 06.11» — дата
            continue
        v = _num(m.group(1))
        if unit.startswith(("тыс", "т.р", "к", "k")) or (not unit and v < 1000):
            v *= 1000
        if v >= 100:
            return int(v)
    return None


def parse_deadline(t: str, today: date) -> date | None:
    if "послезавтра" in t:
        return today + timedelta(days=2)
    if "завтра" in t:
        return today + timedelta(days=1)
    m = re.search(r"(?:за|в течение|в течении)\s*(\d+)\s*(?:дн|день|дня)", t)
    if m:
        return today + timedelta(days=int(m.group(1)))
    m = re.search(r"до\s+(\d{1,2})\.(\d{1,2})", t)
    if m:
        d = date(today.year, int(m.group(2)), int(m.group(1)))
        return d if d >= today else date(today.year + 1, d.month, d.day)
    m = re.search(r"до\s+(\d{1,2})\s+([а-я]+)", t)
    if m:
        for stem, month in MONTHS.items():
            if m.group(2).startswith(stem):
                d = date(today.year, month, int(m.group(1)))
                return d if d >= today else date(today.year + 1, month, d.day)
    m = re.search(r"(?:до|к)\s+(понедельник|вторник|сред|четверг|пятниц|суббот|воскресень)", t)
    if m:
        wd = WEEKDAYS[m.group(1)]
        delta = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    if "на этой неделе" in t:
        return today + timedelta(days=6 - today.weekday() or 7)
    return None


def parse_warranty(t: str) -> int | None:
    m = re.search(r"гаранти\w*\s*(?:от|не меньше|не менее|минимум)?\s*(\d+)\s*(год|лет|мес)", t)
    if m:
        n = int(m.group(1))
        return n * 12 if m.group(2) in ("год", "лет") else n
    if re.search(r"с гаранти|гаранти\w* обязат|нужна гаранти|официальн\w* гаранти", t):
        return 1
    return None


def parse_capacity(t: str) -> int | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(тб|tb|терабайт\w*|гб|gb|гигабайт\w*)", t)
    if not m:
        return None
    v = _num(m.group(1))
    return int(round(v * 1000)) if m.group(2).startswith(("т", "t")) else int(v)


def parse(text: str, today: date) -> Constraints:
    t = text.lower().replace("ё", "е").replace("  ", " ")
    for alias, canon in ALIASES.items():
        t = t.replace(alias, canon)
    t_brand = text.lower()
    category = next((c for c, rx in CATEGORIES if re.search(rx, t)), "other")
    brand = next((b for b, rx in BRANDS.items() if re.search(rx, t_brand)), None)
    keywords = [mdl for mdl in MODELS if re.search(r"(?<![\w-])" + re.escape(mdl) + r"(?![\w-])", t)]
    if "wh-1000xm5" in keywords and "xm5" in keywords:
        keywords.remove("xm5")
    if category == "other" and keywords:
        category = MODEL_CATEGORY[keywords[0]]
    if category == "other" and brand == "Sony":
        category = "headphones"
    return Constraints(
        category=category,
        brand=brand,
        capacity_gb=parse_capacity(t) if category in ("ssd", "hdd", "ram", "other") else None,
        max_total_rub=parse_budget(t),
        deliver_by=parse_deadline(t, today),
        min_warranty_months=parse_warranty(t),
        keywords=keywords,
    )
