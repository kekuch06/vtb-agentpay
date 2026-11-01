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
