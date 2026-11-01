"""Секретный ключ сервера для HMAC: кодов подтверждения, хешей заказов и цепочки журнала.

Берётся из AGENTPAY_SECRET_KEY (не короче 32 символов). Если переменная не задана — генерируется один раз
и хранится в файле backend/data/secret.key с правами 0600. Ключ лежит вне базы данных: имея доступ только
к базе, нельзя ни пересчитать цепочку журнала после правки записей, ни проверить код по его хешу.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

KEY_FILE = Path(os.getenv("AGENTPAY_SECRET_KEY_FILE", "") or Path(__file__).resolve().parent.parent / "data" / "secret.key")
_key: bytes | None = None


def key() -> bytes:
    global _key
    if _key is None:
        env = os.getenv("AGENTPAY_SECRET_KEY", "")
        if env:
            if len(env) < 32:
                raise ValueError("AGENTPAY_SECRET_KEY короче 32 символов")
            _key = env.encode()
        else:
            if not KEY_FILE.exists():
                KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_hex(32))
            _key = KEY_FILE.read_text().strip().encode()
    return _key


def mac(*parts: str) -> str:
    return hmac.new(key(), "|".join(parts).encode(), hashlib.sha256).hexdigest()
