"""Хранилище платёжного контура и журнала. По умолчанию — файл SQLite backend/data/agentpay.sqlite3.

Каждая операция с деньгами выполняется в одной транзакции под блокировкой записи: проверки Policy Engine,
счётчик попыток ввода кода и запись платежа видят одно и то же состояние, даже если запросы пришли
параллельно (FastAPI исполняет синхронные эндпоинты в пуле потоков) или из нескольких процессов.
Журнал пишется в той же транзакции: запись о платеже и сам платёж фиксируются вместе или не фиксируются вовсе.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "agentpay.sqlite3"

SCHEMA = [
    # owner — клиент (хеш сессии): объекты другого клиента для API не существуют.
    """CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL, doc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY, owner TEXT NOT NULL, task_id TEXT NOT NULL, doc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS challenges (
        challenge_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, used INTEGER NOT NULL, doc TEXT NOT NULL)""",
    # UNIQUE — вторая линия защиты: даже при ошибке в коде БД не примет второй платёж по заказу или поручению.
    """CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
        bank_day TEXT NOT NULL, amount_kop BIGINT NOT NULL CHECK (amount_kop > 0), doc TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS challenges_order ON challenges (order_id)",
    # Отдельный канал доставки кода: в продукте — push на устройство клиента, здесь — его sandbox-имитация.
    """CREATE TABLE IF NOT EXISTS device_inbox (
        challenge_id TEXT PRIMARY KEY, owner TEXT NOT NULL, doc TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS payments_day ON payments (owner, bank_day)",
    "CREATE TABLE IF NOT EXISTS settings (owner TEXT PRIMARY KEY, doc TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, actor TEXT NOT NULL, zone TEXT NOT NULL, action TEXT NOT NULL,
        owner TEXT, task_id TEXT, order_id TEXT, data TEXT NOT NULL,
        prev_hash TEXT NOT NULL, hash TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS audit_owner ON audit (owner, task_id)",
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
]

_lock = threading.RLock()
_target: str | Path = os.getenv("AGENTPAY_DATABASE_PATH") or DEFAULT
_conn: sqlite3.Connection | None = None
_depth = 0


def current() -> str | Path:
    return _target


def use(target: str | Path) -> None:
    """Переключает хранилище (тесты, прогон метрик) и закрывает текущее соединение."""
    global _conn, _target
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _target = None, target


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = Path(_target)
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: транзакции открываются явно, BEGIN IMMEDIATE сразу берёт блокировку записи.
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA:
            _conn.execute(stmt)
    return _conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Транзакция под блокировкой записи. Вложенный вызов присоединяется к внешней транзакции."""
    global _depth
    with _lock:
        conn = _connect()
        if _depth == 0:
            conn.execute("BEGIN IMMEDIATE")
        _depth += 1
        try:
            yield conn
        except BaseException:
            _depth -= 1
            if _depth == 0:
                conn.execute("ROLLBACK")
            raise
        _depth -= 1
        if _depth == 0:
            conn.execute("COMMIT")


def query(sql: str, params: tuple = ()) -> list[tuple[Any, ...]]:
    with tx() as c:
        return c.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> None:
    with tx() as c:
        c.execute(sql, params)
