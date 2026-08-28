"""清水房本地日 loop（`docs/roughcast-quality-ranking.md` §六 phase 5 / 第 1 期出口）。

**目的**:让 `store_media` 进程在本地机器上无人值守跑很多天,无需依赖任何
外部调度器(Windows 计划任务 / cron / cloud push 都不在范围)。云推送仍是
第 5 期的待办,本地跑通后再上云。

**每天做的事**(顺序硬定,绝不能换):
1. 队列 A — 如果今天**还没起过** A run(任何状态都算,manual / loop 都行)
2. Shadow score — 如果最新已发布的 A run 没被最近一个 COMPLETE score run 覆盖
3. 队列 B — 如果今天没起过 B run 且配额 > 0 且窗口还在

A 跳过、S 跳过、B 跳过的判定**都写进 SQLite**(`roughcast_crawl_runs` /
`roughcast_score_runs`),不靠进程内存里的 `_last_run_day`。这样**进程重
启也安全**——同一日内构造一个全新的 `RoughcastDailyLoop`,SQLite 仍记得
今早手动跑过的那一轮,loop 不会再起第二次。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime, timedelta
from typing import Callable, Mapping, Sequence

from app.application.roughcast_crawler import (
    CrawlOutcome,
    RoughcastCrawlConfig,
    RoughcastCrawler,
    crawl_config_from_settings,
)
from app.application.roughcast_queue_b import (
    QueueBOutcome,
    RoughcastQueueBCrawler,
)
from app.application.roughcast_scorer import run_shadow
from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import RoughcastCrawlClient
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMPLETE,
    FAILED,
    PARTIAL,
    QUEUE_B,
    RoughcastRepository,
    RUNNING,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

DAILY_LOOP_THREAD_NAME = "roughcast-daily-loop"
DAILY_LOOP_POLL_SECONDS = 60.0


@dataclass(frozen=True)
class LoopTick:
    a_status: str | None          # COMPLETE / PARTIAL / ABORTED / FAILED / skipped
    a_run_id: int | None
    scored: bool
    score_run_id: int | None
    b_status: str | None
    b_run_id: int | None
    skipped_reason: str | None    # not_due / already_done / disabled / no_window / no_targets


class RoughcastDailyLoop:
    """一个常驻线程:按本地日做 A → Shadow → B 编排。

    `run_tick()` 是幂等的最小单位——**测试**用这个直接断言;**线程**每
    60s 调一次。窗口、A 已跑过、S 已跑过、B 已跑过、disabled,任何一条
    命中都直接 return,不会重复起任何下游 run。
    """

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        queue_a: RoughcastCrawler,
        queue_b: RoughcastQueueBCrawler,
        repository: RoughcastRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.queue_a = queue_a
        self.queue_b = queue_b
        self.repository = repository
        self._clock = clock
        self._sleeper = sleeper or time.sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------- clock utils

    def _local_now(self) -> datetime:
        return self._clock().astimezone(UTC) + self.queue_a.config.timezone

    def _local_day_start(self) -> datetime:
        """Asia/Shanghai 当天 00:00 转回 UTC——`started_at` 存的是 UTC ISO。"""
        local = self._local_now()
        midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight_local - self.queue_a.config.timezone

    # ---------------------------------------------------------- windowing

    def _within_window(self, moment: datetime) -> bool:
        cfg = self.queue_a.config
        return cfg.window_start <= moment.time() <= cfg.window_end

    def _scheduled_start(self, local: datetime) -> datetime:
        """同一天内结果必须稳定,所以缓存进实例——和 `RoughcastCrawler` 一致。

        返回值与传入的 `local` **同一种 tzinfo**(本地 Asia/Shanghai),
        这样 `_due_now` 里的 `local < self._scheduled_start(local)` 是
        tz-aware vs tz-aware 比较,不会撞 `TypeError`。
        """
        if not hasattr(self, "_scheduled_start_cache") or \
                self._scheduled_start_cache is None or \
                self._scheduled_start_cache.date() != local.date():
            # 用本地日 ordinal 作 seed,同一天每次得到同一个 jitter——手动重跑
            # 与 loop 自动跑结果一致,UI 侧不会被「上次 11:40 这次 12:05」晃到。
            rng = random.Random(local.date().toordinal())
            jitter = rng.uniform(0.0, 2.0 * self.queue_a.config.start_jitter_minutes)
            base = datetime.combine(local.date(), self.queue_a.config.window_start)
            # 把 tzinfo 接上,避免与 `local` 比较时类型不一致。
            self._scheduled_start_cache = base.replace(tzinfo=local.tzinfo) + timedelta(
                minutes=jitter
            )
        return self._scheduled_start_cache

    def _due_now(self) -> tuple[bool, str | None]:
        local = self._local_now()
        if not self._within_window(local):
            return False, "no_window"
        if local < self._scheduled_start(local):
            return False, "not_due"
        return True, None

    # ---------------------------------------------------------- helpers

    def _has_a_run_today(self) -> bool:
        rows = self.repository.runs_for_queue_since("A", self._local_day_start())
        return bool(rows)

    def _has_b_run_today(self) -> bool:
        rows = self.repository.runs_for_queue_since(QUEUE_B, self._local_day_start())
        return bool(rows)

    def _has_a_blocking_run_today(self) -> bool:
        """本地日 A run 中是否含 ABORTED / FAILED / RUNNING——这些状态下 B 不应起。

        401 / `CRM_AUTH_REQUIRED` 这类熔断会写成 ABORTED,进程崩溃前还停在
        RUNNING 的会被 `reap_stale_runs` 改成 FAILED——三种都意味着 CRM 会话
        已死或不可信,同一天再起 B 只会继续打上游、刷配额。判定**必须**走
        SQLite 而不是 `_last_a_status` 之类的内存字段:进程重启 / 全新 loop
        实例化后,A 段会被 `_has_a_run_today()` 短路成 `skipped`,但上一次
        留下的 ABORTED / FAILED / RUNNING 行仍要让 B 一起停。
        """
        rows = self.repository.runs_for_queue_since("A", self._local_day_start())
        return any(row["status"] in (ABORTED, FAILED, RUNNING) for row in rows)

    def _latest_score_listing_run_id(self) -> int | None:
        """最新 COMPLETE score run 的 `listing_run_id`,None 表示从未评过。"""
        latest = self.repository.latest_score_run()
        if latest is None:
            return None
        value = latest["listing_run_id"]
        return int(value) if value is not None else None

    # ---------------------------------------------------------- public API

    def run_tick(self) -> LoopTick:
        """跑一次 A → score → B 编排。线程与单测都走这里。

        任何阶段异常都**只**记日志,不影响后续阶段——B 仍会尝试;
        这是「线程不能死在一轮」的单测断言的对象。
        """
        # 整轮禁用 → 直接 skip。`SM_ROUGHCAST_QUEUE_B_DAILY_LIMIT=0`
        # 视为 B 段单独禁用,本函数依然走完整结构。
        due, reason = self._due_now()
        if not due:
            return LoopTick(None, None, False, None, None, None, reason or "not_due")

        # ---------- A ----------
        a_status: str | None = None
        a_run_id: int | None = None
        if not self._has_a_run_today():
            a_outcome: CrawlOutcome = self.queue_a.run_once()
            a_run_id = a_outcome.run_id
            a_status = a_outcome.status
        else:
            a_status = "skipped"

        # ---------- score ----------
        scored = False
        score_run_id: int | None = None
        if self.settings.roughcast_score_after_crawl:
            latest_a = self.repository.latest_published_run("A")
            if latest_a is not None:
                latest_a_id = int(latest_a["id"])
                if latest_a["status"] not in (ABORTED, FAILED):
                    already_done = (
                        self._latest_score_listing_run_id() == latest_a_id
                    )
                    if not already_done:
                        try:
                            outcome = run_shadow(self.repository, persist=True)
                            score_run_id = outcome.run_id
                            scored = True
                        except Exception as exc:  # noqa: BLE001
                            logger.exception(
                                "Shadow Run 异常,本 tick 仍尝试队列 B:%s", exc
                            )
        else:
            logger.info("SM_ROUGHCAST_SCORE_AFTER_CRAWL=0,跳过 Shadow Run")

        # ---------- B ----------
        b_status: str | None = None
        b_run_id: int | None = None
        limit = self.settings.roughcast_queue_b_daily_limit
        if limit <= 0:
            b_status = "skipped"
        elif self._has_a_blocking_run_today():
            # 今天的 A 在 ABORTED / FAILED / RUNNING(熔断、窗口、崩溃被 reap
            # 之后)——CRM 会话已死,本 tick 不再打 B,等下一进程 / 下一日重置。
            b_status = "skipped"
        elif self._has_b_run_today():
            b_status = "skipped"
        else:
            try:
                b_outcome: QueueBOutcome | None = self.queue_b.run_once(limit=limit)
            except Exception as exc:  # noqa: BLE001
                logger.exception("队列 B 本 tick 异常:%s", exc)
                b_status = "failed"
            else:
                if b_outcome is None:
                    b_status = "skipped"
                else:
                    b_run_id = b_outcome.run_id
                    b_status = b_outcome.status
        return LoopTick(
            a_status=a_status, a_run_id=a_run_id, scored=scored,
            score_run_id=score_run_id, b_status=b_status, b_run_id=b_run_id,
            skipped_reason=None,
        )

    # ---------------------------------------------------------- thread

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=DAILY_LOOP_THREAD_NAME, daemon=True
        )
        self._thread.start()
        logger.info("%s 线程已启动", DAILY_LOOP_THREAD_NAME)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(DAILY_LOOP_POLL_SECONDS):
            try:
                self.run_tick()
            except Exception:  # noqa: BLE001 —— 线程不能因一轮失败而退出
                logger.exception("%s 本 tick 异常,等待下一次唤醒",
                                 DAILY_LOOP_THREAD_NAME)


# -------------------------------------------------------------- 组装入口


def build_daily_loop(
    settings: Settings, database: Database, *,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> RoughcastDailyLoop:
    """从 `Settings` 装配出 `RoughcastDailyLoop`。

    与 `build_crawler` / `build_queue_b_crawler` 同形:queue B 走
    `enforce_window=True`(日 loop 不开 full-sweep),且 A / B 各持有各
    自的 `RoughcastThrottle` 内部实例,**不共享内存节流对象**——B 的
    `_make_throttle` 是按需现造,`spent_today` 重新从 `crawl_log` 算,
    A 已花的额度自动计入。
    """
    config = crawl_config_from_settings(settings)
    repository = RoughcastRepository(database)
    queue_a = RoughcastCrawler(
        repository,
        RoughcastCrawlClient(settings.crm_connector_base_url, page_size=config.page_size),
        config,
    )
    queue_b = RoughcastQueueBCrawler(
        repository,
        RoughcastCrawlClient(settings.crm_connector_base_url, page_size=config.page_size),
        config,
        enforce_window=True,
    )
    return RoughcastDailyLoop(
        settings, database,
        queue_a=queue_a, queue_b=queue_b, repository=repository,
        clock=clock or (lambda: datetime.now(UTC)),
        sleeper=sleeper,
    )


# 重新导出常用符号,免得 import 写两行。
__all__ = [
    "DAILY_LOOP_THREAD_NAME",
    "LoopTick",
    "RoughcastDailyLoop",
    "build_daily_loop",
]
