"""Журнал действий агента и платёжного контура. Только добавление; записи связаны хеш-цепочкой,
поэтому изменение или удаление любой записи обнаруживается при проверке."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import db


def _hash(prev: str, row: dict) -> str:
    return hashlib.sha256((prev + json.dumps(row, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def log(actor: str, zone: str, action: str, data: dict | None = None, task_id: str | None = None, order_id: str | None = None) -> None:
    # Внутри транзакции операции запись фиксируется вместе с ней; блокировка записи сериализует цепочку.
    with db.tx() as c:
        last = c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "0" * 64
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor, "zone": zone, "action": action,
            "task_id": task_id, "order_id": order_id,
            "data": json.dumps(data or {}, ensure_ascii=False, default=str),
        }
        c.execute(
            "INSERT INTO audit (ts, actor, zone, action, task_id, order_id, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (*row.values(), prev, _hash(prev, row)),
        )


def entries(task_id: str | None = None, limit: int = 200) -> list[dict]:
    if task_id:
        rows = db.query(
            "SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
    else:
        rows = db.query(
            "SELECT * FROM (SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit ORDER BY id DESC LIMIT ?) t ORDER BY id",
            (limit,),
        )
    cols = ["id", "ts", "actor", "zone", "action", "task_id", "order_id", "data", "hash"]
    return [{**dict(zip(cols, r)), "data": json.loads(r[7])} for r in rows]


def verify_chain() -> dict:
    prev = "0" * 64
    n = 0
    for r in db.query("SELECT id, ts, actor, zone, action, task_id, order_id, data, prev_hash, hash FROM audit ORDER BY id"):
        row = dict(zip(["ts", "actor", "zone", "action", "task_id", "order_id", "data"], r[1:8]))
        if r[8] != prev or _hash(prev, row) != r[9]:
            return {"ok": False, "entries": n, "broken_at": r[0]}
        prev, n = r[9], n + 1
    return {"ok": True, "entries": n, "head": prev}
