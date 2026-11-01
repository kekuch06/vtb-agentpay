"""Извлечение ограничений языковой моделью: on-prem через Ollama или любой OpenAI-совместимый API
(vLLM в контуре банка, MLX-сервер, DeepSeek, YandexGPT, прокси к GigaChat).

Модель работает в недоверенной зоне: она видит только текст поручения пользователя, не видит карточки
товаров и не имеет инструментов, связанных с платежом. Её ответ принимается только после проверки схемы.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
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
# Режим рассуждений у моделей вроде Qwen3: auto — выключается для qwen3, off — всегда выключен, on — не трогаем.
# Рассуждение здесь не нужно (задача — извлечь поля), а <think>…</think> перед JSON ломал разбор и в разы удлинял ответ.
THINKING = os.getenv("AGENTPAY_LLM_THINKING", "auto")

SYSTEM = """Ты извлекаешь ограничения покупки из поручения клиента. Сегодня {today} ({weekday}).
Верни только один json-объект по схеме, без пояснений. Правила:
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


_AVAILABLE: tuple[float, bool] = (0.0, False)  # (когда проверяли, доступна ли); 0.0 — ещё не проверяли
AVAILABLE_TTL = 15.0  # секунд: внешний API не опрашивается на каждое поручение
_probing = threading.Lock()


def _set_available(ok: bool) -> None:
    global _AVAILABLE
    _AVAILABLE = (time.monotonic(), ok)


def _refresh_in_background() -> None:
    if not _probing.acquire(blocking=False):
        return  # проверка уже идёт

    def run() -> None:
        try:
            _set_available(_probe())
        finally:
            _probing.release()
    threading.Thread(target=run, daemon=True).start()


def available(block: bool = True) -> bool:
    """Доступна ли модель. Запрос пользователя не ждёт опроса модели: устаревшее значение обновляется в фоне,
    а проверка здоровья (block=False) не ждёт даже самый первый опрос. Иначе модель, которая приняла соединение и
    молчит, задерживала /api/health на 3 с — дольше таймаута healthcheck контейнера."""
    if MODE == "rules":
        return False
    checked_at, ok = _AVAILABLE
    if checked_at == 0.0:
        if not block:
            _refresh_in_background()
            return False
        _set_available(_probe())  # первое поручение после запуска: узнаём, есть ли модель
        return _AVAILABLE[1]
    if time.monotonic() - checked_at >= AVAILABLE_TTL:
        _refresh_in_background()
    return ok


def _probe() -> bool:
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


def no_think() -> bool:
    return THINKING == "off" or (THINKING == "auto" and "qwen3" in MODEL.lower())


def _messages(text: str, today: date, with_schema: bool) -> list[dict]:
    system = SYSTEM.format(today=today.isoformat(), weekday=WEEKDAYS_RU[today.weekday()])
    if with_schema:
        system += "\nJSON-схема ответа:\n" + json.dumps(Constraints.model_json_schema(), ensure_ascii=False)
    if no_think():
        system += "\n/no_think"  # мягкий переключатель Qwen3: работает с любым сервером, даже без chat_template_kwargs
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def _call_ollama(text: str, today: date) -> str:
    r = httpx.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": MODEL, "stream": False, "format": Constraints.model_json_schema(),
              "options": {"temperature": 0}, "messages": _messages(text, today, with_schema=False),
              **({"think": False} if no_think() else {})},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def _call_openai(text: str, today: date) -> str:
    body: dict = {"model": MODEL, "temperature": 0, "max_tokens": 512, "messages": _messages(text, today, with_schema=True),
                  "response_format": {"type": "json_object"}}
    if no_think():
        body["chat_template_kwargs"] = {"enable_thinking": False}  # vLLM, SGLang, mlx_lm.server
    r = httpx.post(f"{BASE_URL}/chat/completions", headers=_auth(), json=body, timeout=TIMEOUT)
    if r.status_code in (400, 422):
        # Часть серверов (LM Studio, старые MLX и прокси) не знает json_object и отклоняет запрос: повторяем без него,
        # формат держит промпт со схемой, а разбор ниже вырезает всё вокруг JSON.
        body.pop("response_format")
        r = httpx.post(f"{BASE_URL}/chat/completions", headers=_auth(), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
FENCE = re.compile(r"```(?:json)?", re.I)


def parse_content(content: str) -> dict:
    """Текст ответа модели → JSON-объект. Вырезается рассуждение <think>…</think> (в том числе без открывающего тега —
    его съедает шаблон чата), ограждение ```json и текст вокруг. Незакрытое рассуждение — обрыв по лимиту токенов:
    ответа в нём нет, даже если внутри черновик JSON."""
    t = THINK_BLOCK.sub("", content)
    low = t.lower()
    if "</think>" in low:
        t = t[low.rindex("</think>") + len("</think>"):]
    elif "<think>" in low:
        t = t[:low.index("<think>")]
    t = FENCE.sub("", t)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", t):
        try:
            obj, _ = decoder.raw_decode(t, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("в ответе модели нет JSON-объекта")


def extract(text: str, today: date) -> Constraints:
    try:
        content = _call_openai(text, today) if PROVIDER == "openai" else _call_ollama(text, today)
    except Exception as e:  # сеть, таймаут, модель не загружена
        # Пользователю — только категория ошибки: текст исключения содержит внутренние адреса контура.
        log.warning("LLM недоступна: %r", e)
        _set_available(False)  # следующие поручения не ждут таймаут, пока фоновый опрос не увидит модель снова
        raise LLMUnavailable(_reason(e)) from e
    try:
        return Constraints.model_validate(parse_content(content))
    except (ValueError, ValidationError) as e:  # ValidationError — подкласс ValueError, указан для ясности
        log.warning("ответ модели не прошёл проверку схемы: %s", e)
        raise LLMUnavailable(f"ответ модели не прошёл проверку схемы ({type(e).__name__})") from e
