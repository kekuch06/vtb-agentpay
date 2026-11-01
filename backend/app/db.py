"""Хранилище платёжного контура и журнала: PostgreSQL, если задан AGENTPAY_DATABASE_URL, иначе файл SQLite
(AGENTPAY_DATABASE_PATH, по умолчанию backend/data/agentpay.sqlite3).

Каждая операция с деньгами выполняется в одной транзакции под блокировкой записи: проверки Policy Engine,
счётчик попыток ввода кода и запись платежа видят одно и то же состояние, даже если запросы пришли
параллельно (FastAPI исполняет синхронные эндпоинты в пуле потоков) или из нескольких процессов
(SQLite — BEGIN IMMEDIATE, PostgreSQL — транзакционная advisory-блокировка).
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
LOCK_KEY = 0x41474E54  # ключ advisory-блокировки записи в PostgreSQL

TABLES = [
    # owner — клиент (хеш сессии): объекты другого клиента для API не существуют.
    """CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL, doc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY, owner TEXT NOT NULL, task_id TEXT NOT NULL, doc TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS challenges (
        challenge_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, used INTEGER NOT NULL, doc TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS challenges_order ON challenges (order_id)",
    # Отдельный канал доставки кода: в продукте — push на устройство клиента, здесь — его sandbox-имитация.
    """CREATE TABLE IF NOT EXISTS device_inbox (
        challenge_id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL, doc TEXT NOT NULL)""",
    # UNIQUE — вторая линия защиты: даже при ошибке в коде БД не примет второй платёж по заказу или поручению.
    """CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
        bank_day TEXT NOT NULL, amount_kop BIGINT NOT NULL CHECK (amount_kop > 0), doc TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS payments_day ON payments (owner, bank_day)",
    "CREATE TABLE IF NOT EXISTS settings (owner TEXT PRIMARY KEY, doc TEXT NOT NULL)",
    # Запросы на повышение лимита: применяются только после кода с устройства клиента.
    """CREATE TABLE IF NOT EXISTS limit_requests (
        request_id TEXT PRIMARY KEY, owner TEXT NOT NULL, used INTEGER NOT NULL, doc TEXT NOT NULL)""",
]
AUDIT_COLUMNS = """ts TEXT NOT NULL, actor TEXT NOT NULL, zone TEXT NOT NULL, action TEXT NOT NULL,
        owner TEXT, task_id TEXT, order_id TEXT, data TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL"""

SCHEMA_SQLITE = TABLES + [
    f"CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, {AUDIT_COLUMNS})",
    "CREATE INDEX IF NOT EXISTS audit_owner ON audit (owner, task_id)",
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
]
SCHEMA_PG = TABLES + [
    f"CREATE TABLE IF NOT EXISTS audit (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, {AUDIT_COLUMNS})",
    "CREATE INDEX IF NOT EXISTS audit_owner ON audit (owner, task_id)",
    """CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS
       $$ BEGIN RAISE EXCEPTION 'audit log is append-only'; END $$""",
    "CREATE OR REPLACE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION audit_append_only()",
    "CREATE OR REPLACE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only()",
]
ALL_TABLES = ["audit", "payments", "device_inbox", "challenges", "limit_requests", "orders", "tasks", "settings"]

_lock = threading.RLock()
_target: str | Path = os.getenv("AGENTPAY_DATABASE_URL") or os.getenv("AGENTPAY_DATABASE_PATH") or DEFAULT
_conn: Any = None
_depth = 0
_generation = 0  # растёт при смене хранилища: кеши, привязанные к содержимому базы, сбрасываются


def generation() -> int:
    return _generation


def current() -> str | Path:
    return _target


def is_pg(target: str | Path | None = None) -> bool:
    return str(_target if target is None else target).startswith(("postgresql://", "postgres://"))


def use(target: str | Path) -> None:
    """Переключает хранилище (тесты, прогон метрик): путь к файлу SQLite или URL postgresql://."""
    global _conn, _target, _generation
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _target = None, target
        _generation += 1


class _Conn:
    """Единый интерфейс поверх sqlite3 и psycopg: запросы пишутся с плейсхолдерами «?»."""

    def __init__(self, raw: Any, pg: bool):
        self.raw, self.pg = raw, pg

    def execute(self, sql: str, params: tuple = ()):
        if self.pg:
            return self.raw.execute(sql.replace("?", "%s"), params or None)
        return self.raw.execute(sql, params)

    def close(self) -> None:
        self.raw.close()


def raw_connect(target: str | Path | None = None) -> _Conn:
    """Новое соединение без схемы и блокировок (тесты на подделку журнала, служебные операции)."""
    target = _target if target is None else target
    if is_pg(target):
        import psycopg  # нужен только для PostgreSQL

        return _Conn(psycopg.connect(str(target), autocommit=True), pg=True)
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: транзакции открываются явно, BEGIN IMMEDIATE сразу берёт блокировку записи.
    return _Conn(sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30), pg=False)


def _connect() -> _Conn:
    global _conn
    if _conn is not None and _conn.pg and _conn.raw.closed:  # PostgreSQL перезапускался — соединяемся заново
        _conn = None
    if _conn is None:
        conn = raw_connect()
        if conn.pg:
            conn.execute("SELECT pg_advisory_lock(?)", (LOCK_KEY,))  # схему создаёт один процесс за раз
            try:
                for stmt in SCHEMA_PG:
                    conn.execute(stmt)
            finally:
                conn.execute("SELECT pg_advisory_unlock(?)", (LOCK_KEY,))
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            for stmt in SCHEMA_SQLITE:
                conn.execute(stmt)
        _conn = conn
    return _conn


@contextmanager
def tx() -> Iterator[_Conn]:
    """Транзакция под блокировкой записи. Вложенный вызов присоединяется к внешней транзакции."""
    global _depth, _conn
    with _lock:
        conn = _connect()
        if _depth == 0:
            if conn.pg:
                try:
                    conn.execute("BEGIN")
                except Exception as e:
                    # Сервер БД перезапускался или соединение оборвала сеть: это видно только на первой команде.
                    # Внутри транзакции ещё ничего не сделано, поэтому переоткрыть соединение и начать заново безопасно.
                    import psycopg
                    if not isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)):
                        raise
                    _conn = None
                    conn = _connect()
                    conn.execute("BEGIN")
                conn.execute("SELECT pg_advisory_xact_lock(?)", (LOCK_KEY,))
            else:
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


def drop_all() -> None:
    """Только для тестов на PostgreSQL: чистая схема перед каждым тестом."""
    use(_target)
    conn = raw_connect()
    try:
        for t in ALL_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    finally:
        conn.close()
