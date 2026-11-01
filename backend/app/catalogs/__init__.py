"""Адаптеры тестовых каталогов. У каждого продавца своя схема данных; адаптер приводит её к Offer.

Каталоги — недоверенный источник: цены могут меняться, описания могут содержать что угодно.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ..models import Offer

DIR = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((DIR / name).read_text(encoding="utf-8"))


MERCHANTS: dict[str, dict] = {k: v for k, v in _load("merchants.json").items() if not k.startswith("_")}
_RAW = {"techmart": _load("techmart.json"), "digistore": _load("digistore.json"), "komplekt": _load("komplekt.json")}
_state: dict[str, dict] = {}
RAW_KEYS = {"techmart": ("items", "sku"), "digistore": ("products", "id"), "komplekt": ("catalog", "article")}

CATEGORY_MAP = {
    "ssd": "ssd", "накопители ssd": "ssd", "hdd": "hdd", "ram": "ram", "оперативная память": "ram",
    "headphones": "headphones", "наушники": "headphones", "monitor": "monitor", "мониторы": "monitor",
}


def reset() -> None:
    _state.clear()
    _state.update(copy.deepcopy(_RAW))


reset()


def _parse_volume_gb(s: str) -> int | None:
    m = re.search(r"([\d.,]+)\s*(тб|tb|гб|gb)", s.lower())
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return int(round(v * 1000)) if m.group(2) in ("тб", "tb") else int(v)


def _parse_warranty_months(s: str) -> int:
    s = s.lower()
    if "пожизн" in s:
        return 120
    if "без" in s:
        return 0
    m = re.search(r"(\d+)\s*(год|лет|мес)", s)
    if not m:
        return 0
    n = int(m.group(1))
    return n * 12 if m.group(2) in ("год", "лет") else n


def _kop(rub) -> int:
    """Цена из каталога в рублях → копейки без потерь (15490 → 1549000, 15490.5 → 1549050).
    Строка, bool или дробная копейка — ошибка карточки, а не повод округлять сумму платежа."""
    if isinstance(rub, bool) or not isinstance(rub, (int, float)):
        raise TypeError(f"цена должна быть числом: {rub!r}")
    kop = Decimal(str(rub)) * 100
    if kop != kop.to_integral_value():
        raise ValueError(f"цена с долей копейки: {rub!r}")
    return int(kop)


def _days(v) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f"некорректный срок доставки: {v!r}")
    return v


def _techmart(it: dict, today: date) -> Offer:
    return Offer(
        offer_id=f"techmart:{it['sku']}", merchant_id="techmart", merchant_name=MERCHANTS["techmart"]["name"],
        sku=it["sku"], title=it["title"], brand=it["brand"], category=it["type"], capacity_gb=it["capacity_gb"],
        price_kop=_kop(it["price_rub"]), delivery_kop=_kop(it["delivery"]["price"]),
        total_kop=_kop(it["price_rub"]) + _kop(it["delivery"]["price"]),
        delivery_date=today + timedelta(days=_days(it["delivery"]["days"])),
        warranty_months=it["warranty_months"], in_stock=it["stock"] > 0, description=it.get("text", ""),
    )


def _digistore(it: dict, today: date) -> Offer:
    price, ship = _kop(it["cost"]["amount"]), _kop(it["shipping"]["cost"])
    return Offer(
        offer_id=f"digistore:{it['id']}", merchant_id="digistore", merchant_name=MERCHANTS["digistore"]["name"],
        sku=it["id"], title=it["name"], brand=it["manufacturer"].title(),
        category=CATEGORY_MAP.get(it["group"].lower(), "other"),
        capacity_gb=_parse_volume_gb(it["specs"].get("Объём", "")),
        price_kop=price, delivery_kop=ship, total_kop=price + ship,
        delivery_date=today + timedelta(days=_days(it["shipping"]["eta_days"])),
        warranty_months=_parse_warranty_months(it["guarantee"]), in_stock=it["available"],
        description=it.get("about", ""),
    )


def _komplekt(it: dict, today: date) -> Offer:
    price, ship = it["price_kop"], it["delivery_price_kop"]
    vol = it["volume_tb"]
    desc = it.get("promo_text", "")
    if it.get("payment_account"):
        desc += f" [реквизиты в карточке: {it['payment_account']}]"
    return Offer(
        offer_id=f"komplekt:{it['article']}", merchant_id="komplekt", merchant_name=MERCHANTS["komplekt"]["name"],
        sku=it["article"], title=it["model_name"], brand=it["vendor"].title(),
        category=CATEGORY_MAP.get(it["kind"].lower(), "other"),
        capacity_gb=int(round(vol * 1000)) if vol is not None else None,
        price_kop=price, delivery_kop=ship, total_kop=price + ship,
        delivery_date=today + timedelta(days=-(-_days(it["delivery_hours"]) // 24)),
        warranty_months=it["warranty_years"] * 12, in_stock=it["qty"] > 0, description=desc,
    )


ADAPTERS = {"techmart": _techmart, "digistore": _digistore, "komplekt": _komplekt}


def scan(merchant_id: str, today: date) -> tuple[list[Offer], int]:
    """Карточки продавца, приведённые к Offer, и число отброшенных. Карточка с другой схемой или недопустимыми
    значениями (цена ≤ 0, отрицательный срок, строка вместо числа) не попадает в выдачу и не роняет поиск."""
    items, _ = RAW_KEYS[merchant_id]
    raw = _state[merchant_id].get(items)
    offers, broken = [], 0
    for it in raw if isinstance(raw, list) else []:
        try:
            offers.append(ADAPTERS[merchant_id](it, today))
        except (KeyError, TypeError, ValueError, AttributeError):  # ValidationError — подкласс ValueError
            broken += 1
    return offers, broken


def search(merchant_id: str, today: date) -> list[Offer]:
    return scan(merchant_id, today)[0]


def fetch_offer(offer_id: str, today: date) -> Offer | None:
    """Повторный запрос актуальной карточки у продавца (используется для перепроверки перед оплатой).
    Если карточка стала некорректной, товара для платежа нет."""
    merchant_id = offer_id.split(":", 1)[0]
    if merchant_id not in ADAPTERS:
        return None
    return next((o for o in search(merchant_id, today) if o.offer_id == offer_id), None)


# --- Сценарии sandbox: имитация изменений на стороне продавца между выбором и оплатой ---

def _find_raw(offer_id: str) -> dict | None:
    merchant_id, _, sku = offer_id.partition(":")
    if merchant_id not in RAW_KEYS or not sku:
        return None
    items, key = RAW_KEYS[merchant_id]
    return next((it for it in _state[merchant_id][items] if it.get(key) == sku), None)


def apply_scenario(name: str, offer_id: str) -> str:
    it = _find_raw(offer_id)
    if it is None:
        raise KeyError(offer_id)
    merchant_id = offer_id.split(":", 1)[0]
    if name == "price_spike":
        if merchant_id == "techmart":
            it["price_rub"] = int(it["price_rub"] * 1.25)
        elif merchant_id == "digistore":
            it["cost"]["amount"] = int(it["cost"]["amount"] * 1.25)
        else:
            it["price_kop"] = int(it["price_kop"] * 1.25)
        return "Продавец поднял цену на 25%"
    if name == "out_of_stock":
        if merchant_id == "digistore":
            it["available"] = False
        elif merchant_id == "techmart":
            it["stock"] = 0
        else:
            it["qty"] = 0
        return "Товар закончился у продавца"
    raise KeyError(name)
