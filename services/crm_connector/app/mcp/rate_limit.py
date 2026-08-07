"""Sliding-window rate limiter shared by the MCP tools.

Keyed by caller subject (the local Windows user for stdio transport) so one
abusive agent session cannot exhaust the upstream quota for everyone else.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Sliding-window rate limiter; ``allow`` returns whether the call may proceed."""

    _WINDOW_SECONDS = 60.0

    def __init__(self, limit_per_window: int) -> None:
        self._limit = max(limit_per_window, 1)
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self._WINDOW_SECONDS
        with self._lock:
            events = self._events.get(key)
            if events is None:
                self._events[key] = [now]
                return True
            alive = [stamp for stamp in events if stamp > window_start]
            if len(alive) >= self._limit:
                self._events[key] = alive
                return False
            alive.append(now)
            self._events[key] = alive
            return True
