"""基于 IP 的访问限流（内存实现，单实例足够）。

规则：
- 同一 IP 每 ``rate_per_minute`` 秒窗口内最多 N 次（默认 2 次/分钟）；
- 同一 IP 滑动 24 小时窗口内最多 M 次（默认 30 次/天）。

多实例部署需替换为 Redis 实现；当前为进程内线程安全实现。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class RateDecision:
    """限流判定结果。"""

    allowed: bool
    reason: str = ""
    retry_after_seconds: int = 0
    remaining_minute: int = 0
    remaining_day: int = 0


class IPRateLimiter:
    """按 IP 的分钟级 + 日级滑动窗口限流器。"""

    def __init__(self, per_minute: int = 2, per_day: int = 30,
                 minute_window: int = 60, day_window: int = 86400) -> None:
        self._per_minute = per_minute
        self._per_day = per_day
        self._minute_window = minute_window
        self._day_window = day_window
        self._minute_log: dict[str, deque[float]] = defaultdict(deque)
        self._day_log: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, ip: str, now: float | None = None) -> RateDecision:
        """检查 ``ip`` 是否允许发起一次请求；允许时记录并返回。"""
        ts = now if now is not None else time.time()
        with self._lock:
            mq = self._evict(self._minute_log[ip], ts, self._minute_window)
            dq = self._evict(self._day_log[ip], ts, self._day_window)

            if len(dq) >= self._per_day:
                retry = int(dq[0] + self._day_window - ts) + 1
                return RateDecision(False, "已达每日请求上限", max(retry, 1),
                                    max(self._per_minute - len(mq), 0),
                                    max(self._per_day - len(dq), 0))
            if len(mq) >= self._per_minute:
                retry = int(mq[0] + self._minute_window - ts) + 1
                return RateDecision(False, "请求过于频繁，请稍后再试",
                                    max(retry, 1),
                                    max(self._per_minute - len(mq), 0),
                                    max(self._per_day - len(dq), 0))

            mq.append(ts)
            dq.append(ts)
            return RateDecision(True, "", 0,
                                max(self._per_minute - len(mq), 0),
                                max(self._per_day - len(dq), 0))

    @staticmethod
    def _evict(q: deque[float], now: float, window: int) -> deque[float]:
        """剔除过期时间戳，返回同一队列。"""
        while q and now - q[0] > window:
            q.popleft()
        return q
