"""Ответ модели → ограничения. Qwen3 по умолчанию рассуждает и начинает ответ с <think>…</think>, MLX-сервер и часть
прокси игнорируют response_format, и модель оборачивает JSON в ```json``` или пишет текст вокруг. Такой ответ
должен разбираться, а ответ без JSON — отклоняться (тогда поручение разбирают правила)."""
import json
from datetime import date

import httpx
import pytest

from app.agent import llm

MON = date(2026, 11, 2)
GOOD = {"category": "ssd", "brand": "Samsung", "capacity_gb": 2000, "max_total_rub": None, "deliver_by": "2026-11-06",
        "min_warranty_months": 1, "keywords": []}
J = json.dumps(GOOD, ensure_ascii=False)


@pytest.mark.parametrize("content", [
    J,
    f"<think>\nНужно извлечь бренд {{Samsung}} и дату.\n</think>\n\n{J}",
    f"<think></think>{J}",
    f"Рассуждение без открывающего тега, его съел шаблон чата.\n</think>\n{J}",
    f"```json\n{J}\n```",
    f"<think>ок</think>\n```json\n{J}\n```",
    f"Вот ограничения: {J} — проверьте.",
    f"Подумаем {{так}} и {{так}}. Ответ: {J}",
    f"<THINK>caps</THINK>{J}",
])
def test_json_is_extracted(content):
    assert llm.parse_content(content) == GOOD


@pytest.mark.parametrize("content", [
    "",
    "<think>модель упёрлась в лимит токенов и не закончила рассуждение {\"category\": \"ssd\"",
    f"<think>рассуждение с черновиком {J} без ответа",
    "не знаю",
    "[1, 2, 3]",
])
def test_no_json_is_rejected(content):
    with pytest.raises(ValueError):
        llm.parse_content(content)


class FakeServer:
    """OpenAI-совместимый сервер: запоминает запросы, отвечает заданным текстом."""

    def __init__(self, content: str, reject_response_format: bool = False):
        self.content, self.reject, self.requests = content, reject_response_format, []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.requests.append(dict(json))  # копия: клиент переиспользует тело запроса
        req = httpx.Request("POST", url)
        if self.reject and "response_format" in json:
            return httpx.Response(400, request=req, json={"error": "response_format is not supported"})
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": self.content}}]})


@pytest.fixture
def openai(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "openai")
    monkeypatch.setattr(llm, "BASE_URL", "http://mlx.local:8081/v1")

    def use(model: str, server: FakeServer, thinking: str = "auto"):
        monkeypatch.setattr(llm, "MODEL", model)
        monkeypatch.setattr(llm, "THINKING", thinking)
        monkeypatch.setattr(llm.httpx, "post", server)
        return server
    return use


def test_qwen3_thinking_is_switched_off_and_think_block_cut(openai):
    s = openai("mlx-community/Qwen3-8B-4bit", FakeServer(f"<think>\n\n</think>\n\n{J}"))
    c = llm.extract("Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы", MON)
    assert c.brand == "Samsung" and c.capacity_gb == 2000
    body = s.requests[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0]["content"].rstrip().endswith("/no_think")


def test_other_models_get_no_qwen_switches(openai):
    s = openai("deepseek-chat", FakeServer(J))
    llm.extract("SSD Samsung 2 ТБ", MON)
    body = s.requests[0]
    assert "chat_template_kwargs" not in body and "/no_think" not in body["messages"][0]["content"]
    assert body["response_format"] == {"type": "json_object"}
    assert "json" in body["messages"][0]["content"]  # DeepSeek в JSON-режиме требует слово json в промпте


def test_server_without_response_format_gets_plain_request(openai):
    s = openai("qwen3-8b", FakeServer(f"```json\n{J}\n```", reject_response_format=True))
    assert llm.extract("SSD Samsung 2 ТБ", MON).brand == "Samsung"
    assert "response_format" in s.requests[0] and "response_format" not in s.requests[1]


def test_answer_without_json_falls_back_to_rules(openai, client):
    openai("qwen3-8b", FakeServer("<think>долгое рассуждение, которое оборвалось по лимиту токенов"))
    import app.agent.llm as m
    m_available = m.available
    try:
        m.available = lambda: True
        t = client.post("/api/tasks", json={"text": "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"}).json()
    finally:
        m.available = m_available
    assert t["parser"] == "rules"
    assert t["recommended_offer_id"]  # разбор правилами довёл поручение до рекомендации


def test_health_does_not_wait_for_silent_model(client, monkeypatch):
    # Было: /api/health ждал ответа модели — модель, которая приняла соединение и молчит, давала 3,1 с при таймауте
    # healthcheck 3 с. Раз в 15 с ту же задержку получало и поручение клиента.
    import time
    monkeypatch.setattr(llm, "MODE", "openai")
    monkeypatch.setattr(llm, "PROVIDER", "openai")
    monkeypatch.setattr(llm, "BASE_URL", "http://model.local/v1")
    monkeypatch.setattr(llm, "_probe", lambda: time.sleep(2) or True)
    for checked_at in (0.0, time.monotonic() - 3600):  # ещё не проверяли; проверка устарела
        monkeypatch.setattr(llm, "_AVAILABLE", (checked_at, False))
        t0 = time.perf_counter()
        assert client.get("/api/health").status_code == 200
        assert time.perf_counter() - t0 < 0.5


def test_network_failure_marks_model_unavailable(openai, monkeypatch):
    import time

    def down(*a, **k):
        raise httpx.ConnectError("нет соединения")
    openai("qwen3-8b", FakeServer(J))
    monkeypatch.setattr(llm.httpx, "post", down)
    monkeypatch.setattr(llm, "MODE", "openai")
    monkeypatch.setattr(llm, "_AVAILABLE", (time.monotonic(), True))
    with pytest.raises(llm.LLMUnavailable):
        llm.extract("SSD Samsung 2 ТБ", MON)
    monkeypatch.setattr(llm, "_probe", lambda: pytest.fail("доступность должна браться из отказа, без нового опроса"))
    assert llm.available() is False  # следующее поручение не ждёт таймаут модели
