"""队列 A:清水房全量采集（`docs/roughcast-quality-ranking.md` §三 / §4.2）。

**只采集,不评分**（第 1 期）。一轮 = 一个 `roughcast_crawl_runs` 行,
终态只有三种,且只有 `COMPLETE` 会发布:

    RUNNING ──翻完全部页──▶ COMPLETE   （唯一允许刷新 listing_current 的终态）
       ├──熔断 / 预算耗尽 / 窗口关闭──▶ ABORTED
       └──异常 / 进程退出─────────────▶ FAILED

时钟、`sleep`、随机数全部注入,所以单测零等待且可复现。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime, timedelta
from typing import Callable

from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import (
    CrawlPage,
    CrawlRequestError,
    RoughcastCrawlClient,
)
from app.infrastructure.roughcast_repository import ABORTED, COMPLETE, FAILED, RoughcastRepository
from app.infrastructure.roughcast_throttle import (
    RoughcastThrottle,
    ThrottleStop,
    plan_budget,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

QUEUE_A = "A"


@dataclass(frozen=True)
class RoughcastCrawlConfig:
    page_size: int = 50
    daily_request_cap: int = 260
    safety_factor: float = 1.15
    retry_reserve: int = 10
    min_request_interval_seconds: float = 20.0
    page_gap_seconds: tuple[float, float] = (25.0, 90.0)
    long_pause_every_pages: tuple[int, int] = (8, 15)
    long_pause_seconds: tuple[float, float] = (180.0, 480.0)
    window_start: dtime = dtime(9, 30)
    window_end: dtime = dtime(19, 0)
    start_jitter_minutes: int = 40
    utc_offset_hours: int = 8          # Asia/Shanghai 无夏令时,固定偏移即精确
    stale_run_max_age_hours: int = 12

    @property
    def timezone(self) -> timedelta:
        return timedelta(hours=self.utc_offset_hours)


@dataclass(frozen=True)
class CrawlOutcome:
    run_id: int
    status: str
    pages_done: int
    pages_expected: int | None
    published: int = 0
    snapshots_inserted: int = 0
    deactivated: int = 0
    requests: int = 0
    reason: str | None = None


@dataclass
class _Pacing:
    """页间节奏。长停顿的周期本身也是随机的,避免形成可预测的节拍。"""

    config: RoughcastCrawlConfig
    rng: random.Random
    pages_until_long_pause: int = field(init=False)

    def __post_init__(self) -> None:
        self.pages_until_long_pause = self.rng.randint(*self.config.long_pause_every_pages)

    def next_gap_seconds(self) -> float:
        self.pages_until_long_pause -= 1
        if self.pages_until_long_pause <= 0:
            self.pages_until_long_pause = self.rng.randint(*self.config.long_pause_every_pages)
            return self.rng.uniform(*self.config.long_pause_seconds)
        return self.rng.uniform(*self.config.page_gap_seconds)


class RoughcastCrawler:
    def __init__(
        self,
        repository: RoughcastRepository,
        client: RoughcastCrawlClient,
        config: RoughcastCrawlConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
    ):
        self.repository = repository
        self.client = client
        self.config = config
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._rng = rng or random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run_day: date | None = None
        self._today_start_at: datetime | None = None

    # ------------------------------------------------------------ one pass

    def run_once(self) -> CrawlOutcome:
        """跑一轮队列 A。手动入口与常驻线程都走这里。"""
        for run_id in self.repository.reap_stale_runs(
            max_age=timedelta(hours=self.config.stale_run_max_age_hours)
        ):
            logger.warning("回收超时未收尾的 run %s,判 FAILED", run_id)

        throttle = RoughcastThrottle(
            self.repository,
            # 起手用硬顶:队列 A 的页数要等第 1 页的 total 才知道,拿不到 total
            # 就没法按第三章的公式推预算。收到 total 后立刻收紧到推导值。
            # 硬顶始终生效,所以这一步不可能超支。
            daily_budget=self.config.daily_request_cap,
            min_interval_seconds=self.config.min_request_interval_seconds,
            timezone=self.config.timezone,
            clock=self._clock,
            monotonic=self._monotonic,
            sleeper=self._sleeper,
        )
        run_id = self.repository.start_run(QUEUE_A)
        logger.info("队列 A run %s 开始,当日已花 %s 次", run_id, throttle.spent_today())
        try:
            return self._crawl(run_id, throttle)
        except ThrottleStop as exc:
            self.repository.abort_run(run_id, exc.reason)
            requests = self.repository.sync_run_request_count(run_id)
            run = self.repository.get_run(run_id)
            logger.warning("run %s 中止(%s):%s", run_id, exc.reason, exc)
            return CrawlOutcome(
                run_id=run_id, status=ABORTED, pages_done=int(run["pages_done"]),
                pages_expected=run["pages_expected"], requests=requests, reason=exc.reason,
            )
        except Exception as exc:  # noqa: BLE001 —— 任何异常都必须留下终态,不能悬挂 RUNNING
            reason = f"{type(exc).__name__}: {exc}"
            self.repository.fail_run(run_id, reason)
            requests = self.repository.sync_run_request_count(run_id)
            run = self.repository.get_run(run_id)
            logger.exception("run %s 失败", run_id)
            return CrawlOutcome(
                run_id=run_id, status=FAILED, pages_done=int(run["pages_done"]),
                pages_expected=run["pages_expected"], requests=requests, reason=reason,
            )

    def _crawl(self, run_id: int, throttle: RoughcastThrottle) -> CrawlOutcome:
        pages_expected: int | None = None
        pages_done = 0
        retries_left = self.config.retry_reserve
        pacing = _Pacing(self.config, self._rng)
        page = 1

        while pages_expected is None or page <= pages_expected:
            self._require_window_open()
            result = self._fetch_page_with_retry(run_id, throttle, page, retries_left)
            crawl_page, retries_left = result

            self.repository.stage_rows(run_id, crawl_page.rows)
            pages_done = page

            if page == 1:
                pages_expected = self._pages_from_total(crawl_page.total)
                self.repository.record_progress(
                    run_id, pages_done=pages_done, pages_expected=pages_expected,
                    upstream_total=crawl_page.total,
                )
                self._tighten_budget(throttle, pages_expected)
            else:
                self.repository.record_progress(run_id, pages_done=pages_done)

            if not crawl_page.has_more:
                # has_more 是权威终止信号。上游 total 会跨页波动(§七.7),
                # 为「total 掉了一行」判一次 ABORTED 是过度反应——把 expected
                # 对齐到实际翻完的页数并记一行日志即可。
                if pages_expected is not None and pages_done != pages_expected:
                    logger.info(
                        "run %s 第 %s 页 has_more=false,但按 total 预期 %s 页;"
                        "以 has_more 为准", run_id, pages_done, pages_expected,
                    )
                pages_expected = pages_done
                self.repository.record_progress(run_id, pages_expected=pages_expected)
                break

            if pages_expected is not None and page >= pages_expected:
                # total 在采集期间涨了。不追这条会一直增长的尾巴,明天那一轮会覆盖。
                logger.info("run %s 已翻满预期 %s 页,但 has_more 仍为 true", run_id, pages_expected)
                break

            self._sleeper(pacing.next_gap_seconds())
            page += 1

        published = self.repository.complete_run(run_id)
        requests = self.repository.sync_run_request_count(run_id)
        logger.info(
            "run %s COMPLETE:%s 页 / %s 次请求 / 发布 %s 套 / 新快照 %s 行 / 下架 %s 套",
            run_id, pages_done, requests, published["published"],
            published["snapshots_inserted"], published["deactivated"],
        )
        return CrawlOutcome(
            run_id=run_id, status=COMPLETE, pages_done=pages_done,
            pages_expected=pages_expected, requests=requests, **published,
        )

    def _fetch_page_with_retry(self, run_id: int, throttle: RoughcastThrottle, page: int,
                               retries_left: int) -> tuple[CrawlPage, int]:
        while True:
            log_id = throttle.acquire(queue=QUEUE_A, target=f"page={page}", run_id=run_id)
            try:
                crawl_page = self.client.search_page(page)
            except CrawlRequestError as exc:
                throttle.failed(
                    log_id, http_status=exc.http_status, error_code=exc.error_code,
                    note=str(exc), queue=QUEUE_A, run_id=run_id,
                )
                if retries_left <= 0:
                    raise
                retries_left -= 1
                logger.warning("run %s 第 %s 页失败(%s),剩余重试 %s 次",
                               run_id, page, exc, retries_left)
                # 重试也走 acquire,所以照样扣预算——第三章的「一个小区 2 页 + 1 次
                # 重试扣 3」就是这个意思。
                self._sleeper(self._rng.uniform(*self.config.page_gap_seconds))
                self._require_window_open()
                continue
            throttle.succeeded(log_id)
            return crawl_page, retries_left

    def _pages_from_total(self, total: int) -> int | None:
        """页数由 total 现算。**66 不得写成常数**(§七.7)。"""
        if total <= 0:
            # total 未知,退回「翻到 has_more=false」。
            return None
        return -(-total // self.client.page_size)

    def _tighten_budget(self, throttle: RoughcastThrottle, pages_expected: int | None) -> None:
        if pages_expected is None:
            return
        plan = plan_budget(
            queue_a_pages=pages_expected,
            queue_b_pages=0,                     # 队列 B 是第 3 期
            retry_reserve=self.config.retry_reserve,
            safety_factor=self.config.safety_factor,
            hard_cap=self.config.daily_request_cap,
        )
        throttle.daily_budget = plan.daily_budget
        logger.info(
            "当日预算收紧为 %s 次(计划 %s 页 + 重试预留 %s,硬顶 %s)",
            plan.daily_budget, pages_expected, self.config.retry_reserve, plan.hard_cap,
        )

    # -------------------------------------------------------------- window

    def _local_now(self) -> datetime:
        return self._clock().astimezone(UTC) + self.config.timezone

    def _within_window(self, moment: datetime) -> bool:
        return self.config.window_start <= moment.time() <= self.config.window_end

    def _require_window_open(self) -> None:
        if not self._within_window(self._local_now()):
            raise WindowClosed(
                f"已超出 {self.config.window_start}–{self.config.window_end} 采集窗口"
            )

    # -------------------------------------------------------------- daemon

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="roughcast-crawler", daemon=True
        )
        self._thread.start()
        logger.info("清水房采集线程已启动")

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(60):
            try:
                if self._due_now():
                    self._last_run_day = self._local_now().date()
                    self.run_once()
            except Exception:  # noqa: BLE001 —— 线程绝不能因一轮失败而退出
                logger.exception("清水房采集线程本轮异常,等待下一次唤醒")

    def _due_now(self) -> bool:
        local = self._local_now()
        if self._last_run_day == local.date():
            return False
        if not self._within_window(local):
            return False
        return local >= self._scheduled_start(local)

    def _scheduled_start(self, local: datetime) -> datetime:
        """每日启动时间抖动。同一天内结果必须稳定,否则会反复重排。

        抖动取 `[0, 2 × jitter]` 而不是 `±jitter`:窗口下沿就是 09:30,
        负向抖动会落到窗口外、被 `_within_window` 直接拒掉,等于白抖。
        """
        if self._today_start_at is None or self._today_start_at.date() != local.date():
            jitter = self._rng.uniform(0.0, 2.0 * self.config.start_jitter_minutes)
            base = datetime.combine(local.date(), self.config.window_start)
            self._today_start_at = base + timedelta(minutes=jitter)
        return self._today_start_at


class WindowClosed(ThrottleStop):
    reason = "window_closed"


# ------------------------------------------------------------------ 组装入口
# 常驻线程（main.py）与手动一次性入口（scripts/roughcast_crawl_once.py）必须用同一套
# 装配代码,否则两条路径的预算/窗口参数会悄悄漂移。


def crawl_config_from_settings(settings: Settings) -> RoughcastCrawlConfig:
    start, end = settings.roughcast_crawl_window
    return RoughcastCrawlConfig(
        page_size=settings.roughcast_crawl_page_size,
        daily_request_cap=settings.roughcast_daily_request_cap,
        safety_factor=settings.roughcast_crawl_safety_factor,
        retry_reserve=settings.roughcast_crawl_retry_reserve,
        min_request_interval_seconds=settings.roughcast_min_request_interval_seconds,
        page_gap_seconds=settings.roughcast_page_gap_seconds,
        long_pause_every_pages=settings.roughcast_long_pause_every_pages,
        long_pause_seconds=settings.roughcast_long_pause_seconds,
        window_start=dtime.fromisoformat(start),
        window_end=dtime.fromisoformat(end),
        start_jitter_minutes=settings.roughcast_crawl_start_jitter_minutes,
        utc_offset_hours=settings.roughcast_crawl_utc_offset_hours,
    )


def build_crawler(settings: Settings, database: Database) -> RoughcastCrawler:
    config = crawl_config_from_settings(settings)
    return RoughcastCrawler(
        RoughcastRepository(database),
        RoughcastCrawlClient(settings.crm_connector_base_url, page_size=config.page_size),
        config,
    )
