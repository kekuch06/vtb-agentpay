"""Миграции схемы отдельно от приложения: python -m app.migrate (в compose — разовый сервис migrate).

Под владельцем таблиц создаётся схема, триггеры журнала и роль приложения. Приложение подключается под этой ролью
с AGENTPAY_DB_MIGRATE=0: в таблицы платёжного контура — чтение и запись, в журнал audit — только SELECT и INSERT.
Тогда даже из процесса приложения нельзя снять триггер журнала, переписать или удалить записи: это может только
владелец, которого у приложения нет.

Переменные: AGENTPAY_DATABASE_URL (под владельцем), AGENTPAY_DB_APP_ROLE и AGENTPAY_DB_APP_PASSWORD (роль приложения;
создаётся, если её нет, пароль обновляется).
"""
from __future__ import annotations

import os
import re
import sys

from . import db

APP_TABLES = [t for t in db.ALL_TABLES if t != "audit"]


def apply(url: str, app_role: str | None = None, app_password: str | None = None) -> None:
    conn = db.raw_connect(url)
    try:
        if not db.is_pg(url):
            for stmt in db.SCHEMA_SQLITE:
                conn.execute(stmt)
            return
        conn.execute("SELECT pg_advisory_lock(?)", (db.LOCK_KEY,))  # два migrate одновременно не мешают друг другу
        try:
            for stmt in db.SCHEMA_PG:
                conn.execute(stmt)
            if app_role:
                _app_role(conn, app_role, app_password)
        finally:
            conn.execute("SELECT pg_advisory_unlock(?)", (db.LOCK_KEY,))
    finally:
        conn.close()


def _app_role(conn: db._Conn, role: str, password: str | None) -> None:
    from psycopg import sql

    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"недопустимое имя роли: {role!r}")
    if not password or len(password) < 16:
        raise ValueError("AGENTPAY_DB_APP_PASSWORD: нужен пароль роли приложения не короче 16 символов")
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = ?", (role,)).fetchone()
    stmt = "ALTER ROLE {} LOGIN PASSWORD {}" if exists else "CREATE ROLE {} LOGIN PASSWORD {}"
    r = sql.Identifier(role)
    conn.raw.execute(sql.SQL(stmt).format(r, sql.Literal(password)))
    conn.raw.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(r))
    conn.raw.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
        sql.SQL(", ").join(map(sql.Identifier, APP_TABLES)), r))
    conn.raw.execute(sql.SQL("GRANT SELECT, INSERT ON audit TO {}").format(r))  # журнал — только добавление
    conn.raw.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(r))


def main() -> None:
    url = os.getenv("AGENTPAY_DATABASE_URL", "")
    if not url:
        sys.exit("AGENTPAY_DATABASE_URL: укажите базу (postgresql://владелец:пароль@хост:порт/база)")
    apply(url, os.getenv("AGENTPAY_DB_APP_ROLE") or None, os.getenv("AGENTPAY_DB_APP_PASSWORD") or None)
    print("схема применена" + (f", роль приложения {os.environ['AGENTPAY_DB_APP_ROLE']} настроена" if os.getenv("AGENTPAY_DB_APP_ROLE") else ""))


if __name__ == "__main__":
    main()
