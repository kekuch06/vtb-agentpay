"""HTTP API VTB AgentPay (MVP). Все платежи — sandbox, реальных списаний нет."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import audit, catalogs, db, store
from .agent import llm, orchestrator
from .models import Task
from .trust import payments

app = FastAPI(title="VTB AgentPay MVP", version="0.1.0")


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
def create_task(body: TaskIn):
    t = orchestrator.run(body.text, today())
    store.save_task(t)
    return t


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: str):
    t = store.get_task(task_id)
    if t is None:
        raise HTTPException(404, "поручение не найдено")
    return t


@app.post("/api/orders")
def create_order(body: OrderIn):
    task = store.get_task(body.task_id)
    if task is None:
        raise HTTPException(404, "поручение не найдено")
    try:
        o = payments.create_order(task, body.offer_id, today())
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"order": o, "policy": payments.evaluate(o, task, today())}


@app.post("/api/orders/{order_id}/checkout")
def checkout(order_id: str):
    try:
        o, results, ch = payments.checkout(order_id, today())
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"order": o, "policy": results, "challenge": ch}


@app.post("/api/orders/{order_id}/confirm")
def confirm(order_id: str, body: ConfirmIn):
    try:
        o, p = payments.confirm(order_id, body.challenge_id, body.code, today())
    except LookupError as e:
        raise HTTPException(404, str(e))
    except payments.PolicyViolation as e:
        raise HTTPException(409, {"message": "платёж заблокирован Policy Engine", "policy": [r.model_dump() for r in e.results]})
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"order": o, "payment": p}


@app.get("/api/audit")
def get_audit(task_id: str | None = None):
    return {"entries": audit.entries(task_id), "chain": audit.verify_chain()}


@app.get("/api/settings")
def get_settings():
    return payments.settings


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    payments.settings["per_operation_limit_rub"] = body.per_operation_limit_rub
    audit.log("user", "user", "settings.changed", body.model_dump())
    return payments.settings


@app.get("/api/catalogs")
def get_catalogs():
    return {m: [o.model_dump(mode="json") for o in catalogs.search(m, today())] for m in catalogs.ADAPTERS}


@app.post("/api/sandbox/scenario")
def scenario(body: ScenarioIn):
    """Имитация событий, которые Policy Engine должен перехватить. Доступно только в sandbox."""
    if body.name == "reset":
        catalogs.reset()
        return {"message": "каталоги восстановлены"}
    if body.name == "payee_swap":
        with db.tx():
            o = store.get_order(body.order_id or "")
            if o is None:
                raise HTTPException(404, "заказ не найден")
            o.payee_account = "40817810000000666666"  # имитация подмены реквизитов после создания заказа
            store.save_order(o)
            audit.log("sandbox", "untrusted", "sandbox.payee_swapped", {"payee_account": o.payee_account}, task_id=o.task_id, order_id=o.order_id)
        return {"message": "реквизиты получателя в заказе подменены"}
    try:
        msg = catalogs.apply_scenario(body.name, body.offer_id or "")
    except KeyError:
        raise HTTPException(400, "неизвестный сценарий или предложение")
    audit.log("sandbox", "untrusted", f"sandbox.{body.name}", {"offer_id": body.offer_id})
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
