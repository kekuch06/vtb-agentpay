"""Схемы данных. Всё, что пересекает границу недоверенной зоны, проходит через эти модели."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """Нормализованное предложение продавца. Поля description/title — недоверенный текст.

    Значения тоже недоверенные: цена ≤ 0, отрицательная гарантия или итог, не равный цене с доставкой,
    отклоняются схемой, и такая карточка не доходит ни до проверки условий, ни до суммы заказа."""

    model_config = ConfigDict(strict=True)

    offer_id: str = Field(max_length=100)
    merchant_id: str = Field(max_length=40)
    merchant_name: str = Field(max_length=100)
    sku: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=300)
    brand: str = Field(max_length=60)
    category: str = Field(max_length=30)
    capacity_gb: int | None = Field(ge=1, le=1_000_000)
    # Деньги — целые копейки: цена 18 487,50 ₽ не должна превращаться в 18 487 ₽ на пути к платежу.
    price_kop: int = Field(gt=0, le=1_000_000_000)
    delivery_kop: int = Field(ge=0, le=100_000_000)
    total_kop: int = Field(gt=0)
    delivery_date: date
    warranty_months: int = Field(ge=0, le=1200)
    in_stock: bool
    description: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def _total_is_price_plus_delivery(self) -> "Offer":
        if self.total_kop != self.price_kop + self.delivery_kop:
            raise ValueError("итог не равен цене с доставкой")
        return self


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
    amount_kop: int = Field(gt=0)
    status: Literal["draft", "awaiting_confirmation", "paid", "rejected", "expired"]
    created_at: datetime
    expires_at: datetime
    order_hash: str
    # Счётчики на заказ, а не на код: новый код не обнуляет число неверных попыток.
    code_attempts: int = 0
    challenges_issued: int = 0


class PolicyResult(BaseModel):
    rule: str
    passed: bool
    detail: str


class Challenge(BaseModel):
    challenge_id: str
    order_id: str
    amount_kop: int
    payee_account: str
    merchant_name: str
    expires_at: datetime
    # Самого кода здесь нет: он уходит отдельным каналом на устройство клиента (в sandbox — /api/sandbox/device).


class DeviceMessage(BaseModel):
    """Push-уведомление на устройство клиента. В sandbox устройство имитирует /api/sandbox/device."""

    challenge_id: str
    order_id: str
    merchant_name: str
    amount_kop: int
    code: str
    expires_at: datetime


class Payment(BaseModel):
    payment_id: str
    order_id: str
    task_id: str
    amount_kop: int
    payee_account: str
    status: Literal["succeeded"]
    created_at: datetime
