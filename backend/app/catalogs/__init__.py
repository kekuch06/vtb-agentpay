"""Адаптеры тестовых каталогов. У каждого продавца своя схема данных; адаптер приводит её к Offer.

Каталоги — недоверенный источник: цены могут меняться, описания могут содержать что угодно.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, timedelta
from pathlib import Path

from ..models import Offer

DIR = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((DIR / name).read_text(encoding="utf-8"))


MERCHANTS: dict[str, dict] = {k: v for k, v in _load("merchants.json").items() if not k.startswith("_")}
_RAW = {"techmart": _load("techmart.json"), "digistore": _load("digistore.json"), "komplekt": _load("komplekt.json")}
_state: dict[str, dict] = {}

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


def _techmart(today: date) -> list[Offer]:
    out = []
    for it in _state["techmart"]["items"]:
        out.append(Offer(
            offer_id=f"techmart:{it['sku']}", merchant_id="techmart", merchant_name=MERCHANTS["techmart"]["name"],
            sku=it["sku"], title=it["title"], brand=it["brand"], category=it["type"], capacity_gb=it["capacity_gb"],
            price_rub=it["price_rub"], delivery_rub=it["delivery"]["price"],
            total_rub=it["price_rub"] + it["delivery"]["price"],
            delivery_date=today + timedelta(days=it["delivery"]["days"]),
            warranty_months=it["warranty_months"], in_stock=it["stock"] > 0, description=it.get("text", ""),
        ))
    return out


def _digistore(today: date) -> list[Offer]:
    out = []
    for it in _state["digistore"]["products"]:
        price, ship = it["cost"]["amount"], it["shipping"]["cost"]
        out.append(Offer(
            offer_id=f"digistore:{it['id']}", merchant_id="digistore", merchant_name=MERCHANTS["digistore"]["name"],
            sku=it["id"], title=it["name"], brand=it["manufacturer"].title(),
            category=CATEGORY_MAP.get(it["group"].lower(), "other"),
            capacity_gb=_parse_volume_gb(it["specs"].get("Объём", "")),
            price_rub=price, delivery_rub=ship, total_rub=price + ship,
            delivery_date=today + timedelta(days=it["shipping"]["eta_days"]),
            warranty_months=_parse_warranty_months(it["guarantee"]), in_stock=it["available"],
            description=it.get("about", ""),
        ))
    return out


def _komplekt(today: date) -> list[Offer]:
    out = []
    for it in _state["komplekt"]["catalog"]:
        price, ship = it["price_kop"] // 100, it["delivery_price_kop"] // 100
        vol = it["volume_tb"]
        desc = it.get("promo_text", "")
        if it.get("payment_account"):
            desc += f" [реквизиты в карточке: {it['payment_account']}]"
        out.append(Offer(
            offer_id=f"komplekt:{it['article']}", merchant_id="komplekt", merchant_name=MERCHANTS["komplekt"]["name"],
            sku=it["article"], title=it["model_name"], brand=it["vendor"].title(),
            category=CATEGORY_MAP.get(it["kind"].lower(), "other"),
            capacity_gb=int(round(vol * 1000)) if vol is not None else None,
            price_rub=price, delivery_rub=ship, total_rub=price + ship,
            delivery_date=today + timedelta(days=-(-it["delivery_hours"] // 24)),
            warranty_months=it["warranty_years"] * 12, in_stock=it["qty"] > 0, description=desc,
        ))
    return out


ADAPTERS = {"techmart": _techmart, "digistore": _digistore, "komplekt": _komplekt}


def search(merchant_id: str, today: date) -> list[Offer]:
    return ADAPTERS[merchant_id](today)


def fetch_offer(offer_id: str, today: date) -> Offer | None:
    """Повторный запрос актуальной карточки у продавца (используется для перепроверки перед оплатой)."""
    merchant_id = offer_id.split(":", 1)[0]
    if merchant_id not in ADAPTERS:
        return None
    return next((o for o in ADAPTERS[merchant_id](today) if o.offer_id == offer_id), None)


# --- Сценарии sandbox: имитация изменений на стороне продавца между выбором и оплатой ---

def _find_raw(offer_id: str) -> dict | None:
    merchant_id, sku = offer_id.split(":", 1)
    items = {"techmart": ("items", "sku"), "digistore": ("products", "id"), "komplekt": ("catalog", "article")}[merchant_id]
    return next((it for it in _state[merchant_id][items[0]] if it[items[1]] == sku), None)


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
