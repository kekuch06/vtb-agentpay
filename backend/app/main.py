"""HTTP API VTB AgentPay (MVP). Все платежи — sandbox, реальных списаний нет."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from . import audit, catalogs, keys, ratelimit, sandbox, store
from .agent import llm, orchestrator
from .models import Task
from .trust import payments

# Ключ HMAC проверяется при запуске: с неверным ключом процесс не поднимается, а не отвечает 500 на каждый запрос.
keys.key()

# Swagger UI загружает скрипты с CDN — в закрытом контуре это внешний ресурс, поэтому по умолчанию выключен.
API_DOCS = os.getenv("AGENTPAY_API_DOCS", "0") == "1"
# Схема API тоже только с документацией: в закрытом контуре это карта эндпоинтов для того, кто ищет, куда стучаться.
app = FastAPI(title="VTB AgentPay MVP", version="0.1.0", docs_url="/docs" if API_DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if API_DOCS else None)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

# Клиент — анонимная сессия в cookie (в продукте — аутентификация в ВТБ Онлайн). Все объекты привязаны к хешу
# сессии: чужое поручение, заказ или запись журнала для API не существуют. SameSite=Strict закрывает CSRF.
SESSION_COOKIE = "agentpay_session"
COOKIE_SECURE = os.getenv("AGENTPAY_COOKIE_SECURE", "0") == "1"


@app.middleware("http")
async def session(request: Request, call_next):
    token = request.cookies.get(SESSION_COOKIE, "")
    valid = bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", token))
    # Сессию открывает только изменяющий запрос. Чтение без cookie получает пустого временного клиента и cookie
    # не ставит: иначе опоздавший ответ на стартовый GET перезаписывал cookie сессии, в которой уже создано поручение.
    fresh = not valid and request.method not in ("GET", "HEAD", "OPTIONS")
    if fresh and (wait := ratelimit.check((ratelimit.SESSIONS_IP, _ip(request)))):
        return _too_many(wait)
    if not valid:
        token = secrets.token_urlsafe(32)
    request.state.owner = hashlib.sha256(token.encode()).hexdigest()[:32]
    response = await call_next(request)
    # Окно подтверждения платежа нельзя встроить в чужую страницу (clickjacking), внешние скрипты не выполняются.
    if not (API_DOCS and request.url.path == "/docs"):
        response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if fresh:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=COOKIE_SECURE, path="/")
    return response


class StaticGZip(GZipMiddleware):
    """Сжатие только статики интерфейса. Ответы API не сжимаются: в них коды подтверждения и данные заказа, а сжатие
    рядом с данными, которые влияют на запрос, открывает утечку по длине ответа (класс атак BREACH)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(StaticGZip, minimum_size=1024)

MAX_BODY = 64 * 1024  # самое длинное законное тело — поручение до 500 символов


class BodyLimit:
    """413 на тело запроса к API больше MAX_BODY — и по Content-Length, и при передаче по частям без него.
    За nginx стенда это уже ограничено, но make run и любой другой прокси не должны читать в память мегабайты."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None:  # объявленную длину сервер не даст превысить — достаточно проверить её
            if not length.isdigit() or int(length) > MAX_BODY:
                return await self._reject(send)
            return await self.app(scope, receive, send)
        # Без Content-Length (передача по частям) тело читается здесь, до приложения, и не дальше лимита.
        chunks, seen, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            seen += len(chunks[-1])
            if seen > MAX_BODY:
                return await self._reject(send)
            more = message.get("more_body", False)
        body, replayed = b"".join(chunks), False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send):
        body = '{"detail":"запрос слишком большой"}'.encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


app.add_middleware(BodyLimit)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _too_many(wait: float) -> JSONResponse:
    seconds = max(1, int(wait + 0.999))
    return JSONResponse({"detail": f"слишком много запросов, повторите через {seconds} с"}, status_code=429,
                        headers={"Retry-After": str(seconds), "Cache-Control": "no-store"})


def owner(request: Request) -> str:
    return request.state.owner


def today() -> date:
    fixed = os.getenv("AGENTPAY_TODAY")
    return date.fromisoformat(fixed) if fixed else date.today()


class TaskIn(BaseModel):
    text: str = Field(min_length=3, max_length=500)


class OrderIn(BaseModel):
    task_id: str
    offer_id: str


class ConfirmIn(BaseModel):
    challenge_id: str
    code: str = Field(pattern=r"^\d{6}$")


class ScenarioIn(BaseModel):
    name: Literal["price_spike", "out_of_stock", "payee_swap", "code_expired", "reset"]
    offer_id: str | None = None
    order_id: str | None = None


class SettingsIn(BaseModel):
    per_operation_limit_rub: int = Field(ge=100, le=1_000_000)


class LimitConfirmIn(BaseModel):
    request_id: str = Field(max_length=40)
    code: str = Field(pattern=r"^\d{6}$")


@app.get("/api/health")
def health():
    return {"ok": True, "llm": {"mode": llm.MODE, "model": llm.MODEL, "available": llm.available(block=False)}, "today": today().isoformat(),
            "sandbox": True}


@app.post("/api/tasks", response_model=Task, responses={429: {"description": "слишком много поручений"}})
def create_task(body: TaskIn, request: Request, me: str = Depends(owner)):
    # Поручение нагружает модель: лимит на сессию и на адрес, чтобы новая сессия на каждое поручение его не обходила.
    if wait := ratelimit.check((ratelimit.TASKS, me), (ratelimit.TASKS_IP, _ip(request))):
        return _too_many(wait)
    t = orchestrator.run(body.text, today(), owner=me)
    store.save_task(me, t)
    return t


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, me: str = Depends(owner)):
    t = store.get_task(me, task_id)
    if t is None:
        raise HTTPException(404, "поручение не найдено")
    return t


@app.post("/api/orders")
def create_order(body: OrderIn, me: str = Depends(owner)):
    task = store.get_task(me, body.task_id)
    if task is None:
        raise HTTPException(404, "поручение не найдено")
    try:
        o = payments.create_order(me, task, body.offer_id, today())
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"order": o, "policy": payments.evaluate(me, o, task, today())}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str, me: str = Depends(owner)):
    """Актуальное состояние заказа: после отказа подтверждения интерфейс берёт статус у сервера, а не угадывает по тексту ошибки."""
    o = store.get_order(me, order_id)
    if o is None:
        raise HTTPException(404, "заказ не найден")
    return {"order": o}


@app.post("/api/orders/{order_id}/checkout")
def checkout(order_id: str, me: str = Depends(owner)):
    try:
        o, results, ch = payments.checkout(me, order_id, today())
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"order": o, "policy": results, "challenge": ch}


@app.post("/api/orders/{order_id}/confirm")
def confirm(order_id: str, body: ConfirmIn, me: str = Depends(owner)):
    try:
        o, p = payments.confirm(me, order_id, body.challenge_id, body.code, today())
    except LookupError as e:
        raise HTTPException(404, str(e))
    except payments.PolicyViolation as e:
        raise HTTPException(409, {"message": "платёж заблокирован Policy Engine", "policy": [r.model_dump() for r in e.results]})
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"order": o, "payment": p}


@app.get("/api/audit")
def get_audit(task_id: str | None = None, me: str = Depends(owner)):
    # Клиенту — только признак целостности: число записей и голова цепочки описывают активность всего банка.
    return {"entries": audit.entries(me, task_id), "chain": {"ok": audit.chain_ok()}}


@app.get("/api/sandbox/device")
def device(me: str = Depends(owner)):
    """Имитация устройства клиента: push-уведомления с кодами подтверждения. В продукте этого эндпоинта нет —
    код приходит в приложение банка на зарегистрированное устройство, а не в канал, через который создаётся платёж."""
    return {"messages": store.device_messages(me)}


def _limits(me: str) -> dict:
    # Сколько уже оплачено за банковский день — для интерфейса: клиент видит, сколько осталось до дневного лимита.
    return {**payments.settings_for(me), "spent_today_kop": store.spent_on(me, payments.bank_day(payments._now()))}


@app.get("/api/settings")
def get_settings(me: str = Depends(owner)):
    return _limits(me)


@app.put("/api/settings")
def put_settings(body: SettingsIn, me: str = Depends(owner)):
    """Понижение лимита — 200 и новые лимиты. Повышение — 202 и запрос подтверждения: код уходит на устройство."""
    request = payments.change_limit(me, body.per_operation_limit_rub)
    if request is None:
        return _limits(me)
    return JSONResponse({**_limits(me), "confirmation": request}, status_code=202)


@app.post("/api/settings/confirm")
def confirm_settings(body: LimitConfirmIn, me: str = Depends(owner)):
    try:
        payments.confirm_limit(me, body.request_id, body.code)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return _limits(me)


@app.get("/api/catalogs")
def get_catalogs(me: str = Depends(owner)):
    return {m: [o.model_dump(mode="json") for o in catalogs.search(m, today(), me)] for m in catalogs.ADAPTERS}


@app.post("/api/sandbox/scenario")
def scenario(body: ScenarioIn, me: str = Depends(owner)):
    """Имитация событий, которые Policy Engine должен перехватить. Доступно только в sandbox и только для своего клиента."""
    try:
        return {"message": sandbox.run(me, body.name, body.offer_id or "", body.order_id or "")}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(400, "неизвестный сценарий или предложение")


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Собранный фронтенд. Отдаются только файлы внутри dist: путь из URL нормализуется и сверяется с корнем,
    иначе «/..%2F..%2Fetc/passwd» читал бы любой файл сервера. Неизвестный путь API — 404, а не index.html."""
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "не найдено")
    root = DIST.resolve()
    if not (root / "index.html").is_file():
        raise HTTPException(404, "фронтенд не собран: make build")
    f = (root / path).resolve()
    if path and f.is_relative_to(root) and f.is_file():
        return FileResponse(f)
    return FileResponse(root / "index.html")
