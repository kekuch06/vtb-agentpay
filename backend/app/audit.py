"""Журнал действий агента и платёжного контура. Только добавление; записи связаны хеш-цепочкой,
поэтому изменение или удаление любой записи обнаруживается при проверке."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.sqlite3"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, actor TEXT NOT NULL, zone TEXT NOT NULL, action TEXT NOT NULL,
                task_id TEXT, order_id TEXT, data TEXT NOT NULL,
                prev_hash TEXT NOT NULL, hash TEXT NOT NULL)"""
        )
        _conn.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )
        _conn.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
            "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"
        )
    return _conn


def use_path(path: Path) -> None:
    """Переключает журнал на другой файл (тесты, прогон метрик)."""
    global _conn, DB_PATH
    if _conn is not None:
        _conn.close()
    _conn, DB_PATH = None, path


def _hash(prev: str, row: dict) -> str:
    return hashlib.sha256((prev + json.dumps(row, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def log(actor: str, zone: str, action: str, data: dict | None = None, task_id: str | None = None, order_id: str | None = None) -> None:
    with _lock:
        db = _db()
        last = db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "0" * 64
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor, "zone": zone, "action": action,
            "task_id": task_id, "order_id": order_id,
            "data": json.dumps(data or {}, ensure_ascii=False, default=str),
        }
        db.execute(
            "INSERT INTO audit (ts, actor, zone, action, task_id, order_id, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (*row.values(), prev, _hash(prev, row)),
        )
        db.commit()


def entries(task_id: str | None = None, limit: int = 200) -> list[dict]:
    db = _db()
    if task_id:
        q = db.execute(
            "SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    else:
        q = db.execute(
            "SELECT * FROM (SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit ORDER BY id DESC LIMIT ?) ORDER BY id",
            (limit,),
        )
    cols = ["id", "ts", "actor", "zone", "action", "task_id", "order_id", "data", "hash"]
    return [{**dict(zip(cols, r)), "data": json.loads(r[7])} for r in q.fetchall()]


def verify_chain() -> dict:
    db = _db()
    prev = "0" * 64
    n = 0
    for r in db.execute("SELECT id, ts, actor, zone, action, task_id, order_id, data, prev_hash, hash FROM audit ORDER BY id"):
        row = dict(zip(["ts", "actor", "zone", "action", "task_id", "order_id", "data"], r[1:8]))
        if r[8] != prev or _hash(prev, row) != r[9]:
            return {"ok": False, "entries": n, "broken_at": r[0]}
        prev, n = r[9], n + 1
    return {"ok": True, "entries": n, "head": prev}
