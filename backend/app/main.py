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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import audit, catalogs, db, store
from .agent import llm, orchestrator
from .models import Task
from .trust import payments

# Swagger UI загружает скрипты с CDN — в закрытом контуре это внешний ресурс, поэтому по умолчанию выключен.
API_DOCS = os.getenv("AGENTPAY_API_DOCS", "0") == "1"
app = FastAPI(title="VTB AgentPay MVP", version="0.1.0", docs_url="/docs" if API_DOCS else None, redoc_url=None)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

# Клиент — анонимная сессия в cookie (в продукте — аутентификация в ВТБ Онлайн). Все объекты привязаны к хешу
# сессии: чужое поручение, заказ или запись журнала для API не существуют. SameSite=Strict закрывает CSRF.
SESSION_COOKIE = "agentpay_session"
COOKIE_SECURE = os.getenv("AGENTPAY_COOKIE_SECURE", "0") == "1"


@app.middleware("http")
async def session(request: Request, call_next):
    token = request.cookies.get(SESSION_COOKIE, "")
    fresh = not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    if fresh:
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
    name: Literal["price_spike", "out_of_stock", "payee_swap", "reset"]
    offer_id: str | None = None
    order_id: str | None = None


class SettingsIn(BaseModel):
    per_operation_limit_rub: int = Field(ge=100, le=1_000_000)


@app.get("/api/health")
def health():
    return {"ok": True, "llm": {"mode": llm.MODE, "model": llm.MODEL, "available": llm.available()}, "today": today().isoformat(),
            "sandbox": True}


@app.post("/api/tasks", response_model=Task)
def create_task(body: TaskIn, me: str = Depends(owner)):
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
    return {"entries": audit.entries(me, task_id), "chain": audit.verify_chain()}


@app.get("/api/sandbox/device")
def device(me: str = Depends(owner)):
    """Имитация устройства клиента: push-уведомления с кодами подтверждения. В продукте этого эндпоинта нет —
    код приходит в приложение банка на зарегистрированное устройство, а не в канал, через который создаётся платёж."""
    return {"messages": store.device_messages(me)}


@app.get("/api/settings")
def get_settings(me: str = Depends(owner)):
    return payments.settings_for(me)


@app.put("/api/settings")
def put_settings(body: SettingsIn, me: str = Depends(owner)):
    with db.tx():
        store.put_settings(me, {**store.get_settings(me), **body.model_dump()})
        audit.log("user", "user", "settings.changed", body.model_dump(), owner=me)
    return payments.settings_for(me)


@app.get("/api/catalogs")
def get_catalogs():
    return {m: [o.model_dump(mode="json") for o in catalogs.search(m, today())] for m in catalogs.ADAPTERS}


@app.post("/api/sandbox/scenario")
def scenario(body: ScenarioIn, me: str = Depends(owner)):
    """Имитация событий, которые Policy Engine должен перехватить. Доступно только в sandbox."""
    if body.name == "reset":
        catalogs.reset()
        return {"message": "каталоги восстановлены"}
    if body.name == "payee_swap":
        with db.tx():
            o = store.get_order(me, body.order_id or "")
            if o is None:
                raise HTTPException(404, "заказ не найден")
            o.payee_account = "40817810000000666666"  # имитация подмены реквизитов после создания заказа
            store.save_order(me, o)
            audit.log("sandbox", "untrusted", "sandbox.payee_swapped", {"payee_account": o.payee_account},
                      task_id=o.task_id, order_id=o.order_id, owner=me)
        return {"message": "реквизиты получателя в заказе подменены"}
    try:
        msg = catalogs.apply_scenario(body.name, body.offer_id or "")
    except KeyError:
        raise HTTPException(400, "неизвестный сценарий или предложение")
    audit.log("sandbox", "untrusted", f"sandbox.{body.name}", {"offer_id": body.offer_id}, owner=me)
    return {"message": msg}


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
