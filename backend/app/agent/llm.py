"""Извлечение ограничений локальной (on-prem) моделью через Ollama.

Модель работает в недоверенной зоне: она видит только текст поручения пользователя, не видит карточки
товаров и не имеет инструментов, связанных с платежом. Её ответ принимается только после проверки схемы.
"""
from __future__ import annotations

import json
import os
from datetime import date

import httpx
from pydantic import ValidationError

from ..models import Constraints

OLLAMA_URL = os.getenv("AGENTPAY_OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("AGENTPAY_LLM_MODEL", "qwen2.5:3b")
MODE = os.getenv("AGENTPAY_LLM", "auto")  # auto | ollama | rules

SYSTEM = """Ты извлекаешь ограничения покупки из поручения клиента. Сегодня {today} ({weekday}).
Верни только JSON по схеме. Правила:
- category: ssd, hdd, ram, gpu, monitor, headphones или other.
- brand: производитель латиницей с заглавной буквы (Samsung, Kingston, Crucial, Western Digital, Seagate, Sony) или null.
- capacity_gb: объём в гигабайтах (2 ТБ = 2000), только для накопителей и памяти, иначе null.
- max_total_rub: максимальная сумма с доставкой в рублях ("до 20 тысяч" = 20000) или null.
- deliver_by: крайняя дата доставки в формате YYYY-MM-DD ("до пятницы" = ближайшая будущая пятница) или null.
- min_warranty_months: минимальная гарантия в месяцах; "с гарантией" без срока = 1; иначе null.
- keywords: модели в нижнем регистре, например ["990 evo"], иначе [].
Ничего не придумывай: если условия нет в тексте, ставь null."""

WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class LLMUnavailable(Exception):
    pass


def available() -> bool:
    if MODE == "rules":
        return False
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        names = [m["name"] for m in r.json().get("models", [])]
        return any(n == MODEL or n.startswith(MODEL + ":") for n in names)
    except Exception:
        return False


def extract(text: str, today: date) -> Constraints:
    schema = Constraints.model_json_schema()
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": MODEL,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": SYSTEM.format(today=today.isoformat(), weekday=WEEKDAYS_RU[today.weekday()])},
                    {"role": "user", "content": text},
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
    except Exception as e:  # сеть, таймаут, модель не загружена
        raise LLMUnavailable(str(e)) from e
    try:
        return Constraints.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LLMUnavailable(f"ответ модели не прошёл проверку схемы: {e}") from e
