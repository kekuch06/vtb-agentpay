"""Журнал действий агента и платёжного контура. Только добавление; записи связаны цепочкой HMAC-SHA256
с ключом сервера, поэтому изменение или удаление записи обнаруживается при проверке. Ключ хранится вне базы:
с доступом только к базе цепочку после правки не пересчитать. Удаление записей с конца цепочки по одной базе
не обнаруживается — для этого голову цепочки нужно выгружать во внешнее хранилище (SIEM, WORM)."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from . import db, keys


def _hash(prev: str, row: dict) -> str:
    return keys.mac("audit", prev, json.dumps(row, ensure_ascii=False, sort_keys=True))


def log(actor: str, zone: str, action: str, data: dict | None = None, task_id: str | None = None, order_id: str | None = None,
        owner: str | None = None) -> None:
    # Внутри транзакции операции запись фиксируется вместе с ней; блокировка записи сериализует цепочку.
    with db.tx() as c:
        last = c.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "0" * 64
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor, "zone": zone, "action": action,
            "owner": owner, "task_id": task_id, "order_id": order_id,
            "data": json.dumps(data or {}, ensure_ascii=False, default=str),
        }
        c.execute(
            "INSERT INTO audit (ts, actor, zone, action, owner, task_id, order_id, data, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (*row.values(), prev, _hash(prev, row)),
        )


def entries(owner: str, task_id: str | None = None, limit: int = 200) -> list[dict]:
    """Записи одного клиента: журнал общий, но клиент видит только свои действия."""
    if task_id:
        rows = db.query(
            "SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit WHERE owner = ? AND task_id = ? ORDER BY id",
            (owner, task_id),
        )
    else:
        rows = db.query(
            "SELECT * FROM (SELECT id, ts, actor, zone, action, task_id, order_id, data, hash FROM audit WHERE owner = ? "
            "ORDER BY id DESC LIMIT ?) t ORDER BY id",
            (owner, limit),
        )
    cols = ["id", "ts", "actor", "zone", "action", "task_id", "order_id", "data", "hash"]
    return [{**dict(zip(cols, r)), "data": json.loads(r[7])} for r in rows]


def _verify(after_id: int = 0, prev: str = "0" * 64) -> dict:
    n, last = 0, after_id
    for r in db.query("SELECT id, ts, actor, zone, action, owner, task_id, order_id, data, prev_hash, hash FROM audit "
                      "WHERE id > ? ORDER BY id", (after_id,)):
        row = dict(zip(["ts", "actor", "zone", "action", "owner", "task_id", "order_id", "data"], r[1:9]))
        if r[9] != prev or _hash(prev, row) != r[10]:
            return {"ok": False, "entries": n, "broken_at": r[0]}
        prev, n, last = r[10], n + 1, r[0]
    return {"ok": True, "entries": n, "head": prev, "last_id": last}


def verify_chain() -> dict:
    """Полная проверка всей цепочки: прогон метрик, тесты, служебные проверки."""
    return _verify()


FULL_EVERY = 300.0  # секунд между полными проверками для интерфейса
_chain_cache: dict = {}
_chain_lock = threading.Lock()


def _remember_full(gen: int, r: dict) -> None:
    _chain_cache.update(db=gen, full_at=time.monotonic(), ok=r["ok"], running=False)
    if r["ok"]:
        _chain_cache.update(last_id=r["last_id"], head=r["head"])


def _full_in_background(gen: int) -> None:
    r = _verify()
    with _chain_lock:
        if _chain_cache.get("db") == gen:
            _remember_full(gen, r)


def chain_ok(wait: bool = False) -> bool:
    """Целостность цепочки для интерфейса без прохода по всему журналу банка в запросе пользователя. Новые записи
    проверяются от последней проверенной головы — правка свежей записи видна сразу. Весь журнал перепроверяется
    в фоне не реже раза в FULL_EVERY секунд — правка старой записи с доступом к базе обнаружится при ближайшей
    полной проверке. Первая проверка после запуска и проверка после найденного нарушения — полные и синхронные."""
    with _chain_lock:
        c, gen = _chain_cache, db.generation()
        if wait or c.get("db") != gen or not c.get("ok"):
            r = _verify()
            _remember_full(gen, r)
            return r["ok"]
        if time.monotonic() - c["full_at"] >= FULL_EVERY and not c.get("running"):
            c["running"] = True
            threading.Thread(target=_full_in_background, args=(gen,), daemon=True).start()
        r = _verify(c["last_id"], c["head"])
        c["ok"] = r["ok"]
        if r["ok"] and r["entries"]:
            c.update(last_id=r["last_id"], head=r["head"])
        return r["ok"]
