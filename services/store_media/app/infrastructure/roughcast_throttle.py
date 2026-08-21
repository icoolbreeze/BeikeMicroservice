"""共享节流器:日预算 + 全局最小间隔 + 熔断（`docs/roughcast-quality-ranking.md` §三）。

**定位**:这是内部授权系统的上游负载控制,不是规避检测。目标是让自动任务的访问量
长期低于一个正常业务用户。

三条硬规则,任何采集队列都必须经由本模块发请求:

1. **每产生一次真实上游请求,预算扣 1**,扣减发生在 `acquire()` 里——
   也就是发起请求的那一处,不是任务完成处。一个小区 2 页搜索 + 1 次重试扣 **3**。
2. **已花额度以 `roughcast_crawl_log` 为准现算**,内存只作缓存。
   否则进程重启就把当天已花的额度忘光,硬顶形同虚设。
3. **熔断绝不重试打穿**。触发后当天剩余任务全部取消。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from app.infrastructure.roughcast_repository import RoughcastRepository

logger = logging.getLogger(__name__)

# 熔断的三类即时信号(第三章)。第四类「连续 3 次失败」由计数器判定。
BREAKER_HTTP_STATUSES = frozenset({
    429,  # 上游限流
    401,  # session 失效 —— connector 的 CRM_AUTH_REQUIRED
    502,  # 验证码 / 登录页 —— kecom_session_provider 对非 JSON 的 200 抛 UpstreamChangedError
})
BREAKER_ERROR_CODES = frozenset({"CRM_AUTH_REQUIRED", "CRM_UPSTREAM_CHANGED"})
CONSECUTIVE_FAILURE_LIMIT = 3


class ThrottleStop(RuntimeError):
    """必须停止发起新请求。`reason` 直接落进 `crawl_runs.abort_reason`。"""

    reason = "throttle_stop"


class BudgetExhausted(ThrottleStop):
    reason = "budget_exhausted"


class BreakerOpen(ThrottleStop):
    reason = "breaker_open"


@dataclass(frozen=True)
class BudgetPlan:
    planned_requests: int
    daily_budget: int
    hard_cap: int


def plan_budget(*, queue_a_pages: int, queue_b_pages: int = 0, retry_reserve: int,
                safety_factor: float, hard_cap: int) -> BudgetPlan:
    """第三章的预算公式。**按计划推导,不是常数。**

    V2 把「小区数」直接当成「请求数」,遇到大盘小区多页就静默超支——
    预算显示没超,实际请求数已经翻倍。所以这里的输入必须是**页数**。
    """
    planned = queue_a_pages + queue_b_pages + retry_reserve
    budget = min(-(-int(planned * safety_factor * 1000) // 1000), hard_cap)
    # 上面等价于 ceil(planned × safety_factor),但避开浮点 ceil 在
    # 76 × 1.15 = 87.39999… 这类值上的抖动。
    return BudgetPlan(planned_requests=planned, daily_budget=max(budget, 0), hard_cap=hard_cap)


class RoughcastThrottle:
    """一个日历日内共享的节流阀。跨队列生效。"""

    def __init__(
        self,
        repository: RoughcastRepository,
        *,
        daily_budget: int,
        min_interval_seconds: float,
        timezone: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.repository = repository
        self.daily_budget = daily_budget
        self.min_interval_seconds = min_interval_seconds
        self._timezone = timezone
        self._clock = clock
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_request_at: float | None = None
        self._consecutive_failures = 0
        self._breaker_reason: str | None = None
        self._pending_log_id: int | None = None

    # ------------------------------------------------------------- budget

    def _local_day_start_utc(self) -> datetime:
        """当天 Asia/Shanghai 零点,换算回 UTC。预算按**本地日**结算。"""
        local = self._clock().astimezone(UTC) + self._timezone
        midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight_local - self._timezone

    def spent_today(self) -> int:
        return self.repository.count_requests_since(self._local_day_start_utc())

    def remaining_today(self) -> int:
        return max(self.daily_budget - self.spent_today(), 0)

    # ------------------------------------------------------------ breaker

    @property
    def breaker_reason(self) -> str | None:
        return self._breaker_reason

    def trip(self, reason: str, *, queue: str = "A", run_id: int | None = None) -> None:
        if self._breaker_reason is not None:
            return
        self._breaker_reason = reason
        # 告警:第三章要求熔断必须告警,不能只是安静地停下来。
        logger.error("清水房采集熔断:%s —— 当天剩余任务全部取消,不重试打穿", reason)
        self.repository.log_request(
            run_id=run_id, queue=queue, target="breaker", status="breaker", note=reason
        )

    # ------------------------------------------------------------ acquire

    def acquire(self, *, queue: str, target: str, run_id: int | None = None) -> int:
        """闸门。返回 `crawl_log` 行 id;调用方紧接着就该发出那一次请求。

        预算与 `crawl_log` 行在这里同时产生,所以「预算扣减」和「审计留痕」
        不可能一方漏掉另一方。将来新增任何请求类型都自动计入。
        """
        if self._breaker_reason is not None:
            raise BreakerOpen(self._breaker_reason)
        if self.remaining_today() <= 0:
            raise BudgetExhausted(
                f"当日预算 {self.daily_budget} 已用尽,剩余任务顺延到明天"
            )
        self._wait_for_interval()
        self._last_request_at = self._monotonic()
        self._pending_log_id = self.repository.log_request(
            run_id=run_id, queue=queue, target=target, status="issued"
        )
        return self._pending_log_id

    def _wait_for_interval(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            self._sleeper(remaining)

    # ------------------------------------------------------------- record

    def succeeded(self, log_id: int, *, http_status: int = 200, note: str | None = None) -> None:
        self._consecutive_failures = 0
        self.repository.update_request_log(
            log_id, status="ok", http_status=http_status, note=note
        )

    def failed(self, log_id: int, *, http_status: int | None = None,
               error_code: str | None = None, note: str | None = None,
               queue: str = "A", run_id: int | None = None) -> None:
        """记一次失败,并判断是否该熔断。

        熔断后**不再重试**:调用方下一次 `acquire()` 会直接拿到 `BreakerOpen`。
        """
        self._consecutive_failures += 1
        self.repository.update_request_log(
            log_id, status="failed", http_status=http_status, note=note or error_code
        )
        reason = self._breaker_reason_for(http_status, error_code)
        if reason is not None:
            self.trip(reason, queue=queue, run_id=run_id)

    def _breaker_reason_for(self, http_status: int | None, error_code: str | None) -> str | None:
        if http_status in BREAKER_HTTP_STATUSES:
            return f"http_{http_status}"
        if error_code in BREAKER_ERROR_CODES:
            return f"code_{error_code}"
        if self._consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            return f"consecutive_failures_{self._consecutive_failures}"
        return None
