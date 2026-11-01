"""Схемы данных. Всё, что пересекает границу недоверенной зоны, проходит через эти модели."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Constraints(BaseModel):
    """Структурированные ограничения, извлечённые из поручения (выход недоверенной зоны)."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["ssd", "hdd", "ram", "gpu", "monitor", "headphones", "other"] = "other"
    brand: str | None = Field(default=None, max_length=40)
    capacity_gb: int | None = Field(default=None, ge=1, le=100_000)
    max_total_rub: int | None = Field(default=None, ge=1, le=10_000_000)
    deliver_by: date | None = None
    min_warranty_months: int | None = Field(default=None, ge=0, le=120)
    keywords: list[str] = Field(default_factory=list, max_length=8)


class Offer(BaseModel):
    """Нормализованное предложение продавца. Поля description/title — недоверенный текст."""

    offer_id: str
    merchant_id: str
    merchant_name: str
    sku: str
    title: str
    brand: str
    category: str
    capacity_gb: int | None
    price_rub: int
    delivery_rub: int
    total_rub: int
    delivery_date: date
    warranty_months: int
    in_stock: bool
    description: str = ""


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class CheckedOffer(BaseModel):
    offer: Offer
    checks: list[Check]
    passed: bool
    score: int | None = None  # меньше — лучше; None, если предложение не прошло проверку


class TraceStep(BaseModel):
    step: str
    zone: Literal["untrusted", "trusted", "user"]
    detail: str
    ms: int = 0


class Task(BaseModel):
    task_id: str
    text: str
    created_at: datetime
    parser: str
    constraints: Constraints
    offers: list[CheckedOffer]
    recommended_offer_id: str | None
    explanation: str
    trace: list[TraceStep]


class Order(BaseModel):
    order_id: str
    task_id: str
    offer_id: str
    merchant_id: str
    merchant_name: str
    payee_account: str
    sku: str
    title: str
    amount_rub: int
    status: Literal["draft", "awaiting_confirmation", "paid", "rejected", "expired"]
    created_at: datetime
    expires_at: datetime
    order_hash: str


class PolicyResult(BaseModel):
    rule: str
    passed: bool
    detail: str


class Challenge(BaseModel):
    challenge_id: str
    order_id: str
    amount_rub: int
    payee_account: str
    merchant_name: str
    expires_at: datetime
    # В sandbox код показывается в интерфейсе, имитируя push-уведомление банка.
    sandbox_code: str


class Payment(BaseModel):
    payment_id: str
    order_id: str
    amount_rub: int
    payee_account: str
    status: Literal["succeeded"]
    created_at: datetime
