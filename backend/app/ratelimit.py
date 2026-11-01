"""Ограничение частоты запросов: скользящее окно в памяти процесса.

Поручения нагружают модель — их число ограничено на сессию и на адрес клиента, чтобы новая сессия на каждое
поручение не обходила лимит. Изменяющий запрос без cookie открывает сессию — их число ограничено на адрес.
Адрес — из X-Forwarded-For от nginx стенда (uvicorn --proxy-headers); если backend открыт напрямую, нужно указать
доверенный прокси в --forwarded-allow-ips, иначе заголовок подделывается. Окно у каждого процесса своё: при N
процессах uvicorn фактический лимит — до N раз выше; для промышленного контура — общий счётчик (Redis, шлюз API).
Лимиты: AGENTPAY_RATE_TASKS, AGENTPAY_RATE_TASKS_IP, AGENTPAY_RATE_SESSIONS_IP в виде «число/секунд»;
AGENTPAY_RATE_LIMITS=off выключает (тесты, прогон метрик).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque


class Window:
    """Не больше limit попаданий за period секунд на ключ."""

    def __init__(self, limit: int, period: float):
        self.limit, self.period = limit, period
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Засчитывает попадание. Если лимит исчерпан — не засчитывает и возвращает, через сколько секунд повторить."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.period:
                q.popleft()
            if len(q) >= self.limit:
                return q[0] + self.period - now
            q.append(now)
            if len(self._hits) > 50_000:  # ключи без попаданий в окне не копятся
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - self.period}
            return None


def _window(env: str, default: str) -> Window:
    limit, period = (os.getenv(env) or default).split("/")
    return Window(int(limit), float(period))


ENABLED = os.getenv("AGENTPAY_RATE_LIMITS", "on") != "off"
TASKS = _window("AGENTPAY_RATE_TASKS", "20/60")  # поручений на сессию
TASKS_IP = _window("AGENTPAY_RATE_TASKS_IP", "120/60")  # поручений с одного адреса
SESSIONS_IP = _window("AGENTPAY_RATE_SESSIONS_IP", "60/60")  # новых сессий с одного адреса


def check(*limits: tuple[Window, str]) -> float | None:
    """Через сколько секунд повторить, если хоть один лимит исчерпан; иначе None."""
    if not ENABLED:
        return None
    for window, key in limits:
        wait = window.hit(key)
        if wait is not None:
            return wait
    return None
