"""Извлечение ограничений языковой моделью: on-prem через Ollama или любой OpenAI-совместимый API
(vLLM в контуре банка, YandexGPT, прокси к GigaChat).

Модель работает в недоверенной зоне: она видит только текст поручения пользователя, не видит карточки
товаров и не имеет инструментов, связанных с платежом. Её ответ принимается только после проверки схемы.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

import httpx
from pydantic import ValidationError

from ..models import Constraints

log = logging.getLogger("agentpay.llm")
MODE = os.getenv("AGENTPAY_LLM", "auto")  # auto | ollama | openai | rules
OLLAMA_URL = os.getenv("AGENTPAY_OLLAMA_URL", "http://localhost:11434")
BASE_URL = os.getenv("AGENTPAY_LLM_BASE_URL", "").rstrip("/")  # OpenAI-совместимый endpoint, например http://vllm:8000/v1
API_KEY = os.getenv("AGENTPAY_LLM_API_KEY", "")
PROVIDER = "openai" if MODE == "openai" or (MODE == "auto" and BASE_URL) else "ollama"
MODEL = os.getenv("AGENTPAY_LLM_MODEL", "qwen2.5:3b")
TIMEOUT = float(os.getenv("AGENTPAY_LLM_TIMEOUT", "60"))

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


def _reason(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "таймаут"
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, httpx.TransportError):
        return "нет соединения"
    return type(e).__name__


def available() -> bool:
    if MODE == "rules":
        return False
    try:
        if PROVIDER == "openai":
            if not BASE_URL:
                return False
            r = httpx.get(f"{BASE_URL}/models", headers=_auth(), timeout=3)
            return r.status_code == 200
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        names = [m["name"] for m in r.json().get("models", [])]
        return any(n == MODEL or n.startswith(MODEL + ":") for n in names)
    except Exception:
        return False


def _auth() -> dict:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


def _messages(text: str, today: date, with_schema: bool) -> list[dict]:
    system = SYSTEM.format(today=today.isoformat(), weekday=WEEKDAYS_RU[today.weekday()])
    if with_schema:
        system += "\nJSON-схема ответа:\n" + json.dumps(Constraints.model_json_schema(), ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def _call_ollama(text: str, today: date) -> str:
    r = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": MODEL, "stream": False, "format": Constraints.model_json_schema(),
              "options": {"temperature": 0}, "messages": _messages(text, today, with_schema=False)},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def _call_openai(text: str, today: date) -> str:
    r = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers=_auth(),
        json={"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
              "messages": _messages(text, today, with_schema=True)},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def extract(text: str, today: date) -> Constraints:
    try:
        content = _call_openai(text, today) if PROVIDER == "openai" else _call_ollama(text, today)
    except Exception as e:  # сеть, таймаут, модель не загружена
        # Пользователю — только категория ошибки: текст исключения содержит внутренние адреса контура.
        log.warning("LLM недоступна: %r", e)
        raise LLMUnavailable(_reason(e)) from e
    try:
        return Constraints.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning("ответ модели не прошёл проверку схемы: %s", e)
        raise LLMUnavailable(f"ответ модели не прошёл проверку схемы ({type(e).__name__})") from e
