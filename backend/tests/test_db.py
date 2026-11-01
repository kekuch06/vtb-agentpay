"""Хранилище переживает перезапуск PostgreSQL: разорванное соединение не превращается в 500 у клиента."""
import pytest

from app import db
from conftest import TEST_DB_URL


@pytest.mark.skipif(not TEST_DB_URL, reason="только PostgreSQL: make test-pg")
def test_request_after_postgres_restart_succeeds(client):
    # Было: первый запрос после перезапуска PostgreSQL (или обрыва соединения сетью, failover) — 500.
    assert client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code == 200
    killer = db.raw_connect()
    try:  # обрыв всех соединений приложения — так выглядит для клиента перезапуск сервера БД
        killer.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                       "WHERE datname = current_database() AND pid <> pg_backend_pid()")
    finally:
        killer.close()
    assert client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code == 200


def _can_create_roles() -> bool:
    con = db.raw_connect(TEST_DB_URL)
    try:
        return bool(con.execute("SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0])
    finally:
        con.close()


@pytest.mark.skipif(not TEST_DB_URL or not _can_create_roles(), reason="PostgreSQL и право CREATEROLE (в CI — есть)")
def test_app_role_cannot_rewrite_audit(client, monkeypatch):
    # Было: приложение работало под владельцем таблиц и могло снять триггер журнала и переписать записи.
    # Стало: миграции — отдельно, под владельцем; приложению — только SELECT и INSERT в audit.
    from urllib.parse import urlsplit, urlunsplit

    from app import migrate
    migrate.apply(TEST_DB_URL, app_role="agentpay_app_test", app_password="app-test-password")
    u = urlsplit(TEST_DB_URL)
    app_url = urlunsplit(u._replace(netloc=f"agentpay_app_test:app-test-password@{u.hostname}:{u.port}"))
    monkeypatch.setattr(db, "MIGRATE_ON_CONNECT", False)
    db.use(app_url)
    try:
        t = client.post("/api/tasks", json={"text": "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"}).json()
        assert t["recommended_offer_id"]  # приложение работает под ограниченной ролью
        con = db.raw_connect(app_url)
        try:
            for stmt in ["UPDATE audit SET data = '{}'", "DELETE FROM audit", "TRUNCATE audit",
                         "DROP TRIGGER audit_no_update ON audit", "ALTER TABLE audit DISABLE TRIGGER ALL"]:
                with pytest.raises(Exception, match="permission denied|must be owner"):
                    con.execute(stmt)
        finally:
            con.close()
    finally:
        db.use(TEST_DB_URL)
