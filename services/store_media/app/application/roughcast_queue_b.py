"""队列 B:小区参考集采集（`docs/roughcast-quality-ranking.md` §三 / §4.2）。

按 `resblock_id` 搜该小区全部在租房源,**不带 fitment 过滤**,行级装修三分后
写入 `roughcast_community_reference_snapshot`。只在 COMPLETE / PARTIAL 时
前移 `reference_run_id`,ABORTED / FAILED 一个字节都不碰参考表。

不写 `listing_current` / `listing_snapshot` / `is_active`——那些是队列 A 的 P。

第一次全量(`run_sweep`)按小区单独收尾:一个小区一轮。12 小时的扫荡中途被杀,
已经 COMPLETE 的小区指针还在,续跑只补 `reference_run_id IS NULL` 的剩余。
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime, timedelta
from typing import Callable, Mapping, Sequence

from app.application.roughcast_crawler import (
    RoughcastCrawlConfig,
    WindowClosed,
    _Pacing,
    crawl_config_from_settings,
)
from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import CrawlPage, CrawlRequestError, RoughcastCrawlClient
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMMUNITY_FAILED,
    COMMUNITY_OVER_CAP,
    COMMUNITY_SUCCESS,
    COMPLETE,
    FAILED,
    QUEUE_B,
    RoughcastRepository,
    RunStateError,
    queue_b_bucket,
)
from app.infrastructure.roughcast_throttle import RoughcastThrottle, ThrottleStop
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

# 单小区 totalCount 硬顶。探针最大 162 行,正常穿不透;穿了说明过滤没生效。
COMMUNITY_TOTAL_HARD_CAP = 1000

# 全量第一次把日硬顶抬到这个数,否则 1425 小区会被 260 掐断。
FULL_SWEEP_REQUEST_CAP = 5000


@dataclass(frozen=True)
class QueueBOutcome:
    run_id: int
    status: str
    community_id: str
    community_name: str
    pages_done: int
    pages_expected: int | None
    requests: int = 0
    reference_rows: int = 0
    reference_r_rows: int = 0
    reason: str | None = None
    community_outcomes: Mapping[str, str] | None = None


@dataclass(frozen=True)
class SweepOutcome:
    communities_targeted: int
    communities_success: int
    communities_failed: int
    communities_aborted: int
    pages_done: int
    requests: int
    reference_rows: int
    reference_r_rows: int
    stopped_reason: str | None = None
    results: tuple[QueueBOutcome, ...] = ()

    @property
    def status(self) -> str:
        if self.stopped_reason:
            return ABORTED
        if self.communities_targeted == 0:
            return COMPLETE
        if self.communities_success == self.communities_targeted:
            return COMPLETE
        if self.communities_success > 0:
            return "PARTIAL"
        return FAILED


class RoughcastQueueBCrawler:
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
        enforce_window: bool = True,
    ):
        self.repository = repository
        self.client = client
        self.config = config
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._rng = rng or random.Random()
        self.enforce_window = enforce_window

    def _make_throttle(self) -> RoughcastThrottle:
        return RoughcastThrottle(
            self.repository,
            daily_budget=self.config.daily_request_cap,
            min_interval_seconds=self.config.min_request_interval_seconds,
            timezone=self.config.timezone,
            clock=self._clock,
            monotonic=self._monotonic,
            sleeper=self._sleeper,
        )

    def run_once(self, *, limit: int | None = None) -> QueueBOutcome | None:
        """日常半月轮转:一轮采 `limit` 个小区,成功/失败写进同一个 run。"""
        targets = self.repository.list_queue_b_targets(limit=limit)
        if not targets:
            logger.info("队列 B:没有可搜的小区")
            return None
        return self._run_group(targets)

    def run_sweep(self) -> SweepOutcome:
        """第一次全量:所有还没有参考集的小区,每小区单独收尾。

        中途熔断/预算/窗口 → 停,已 COMPLETE 的小区保留。续跑再调一次即可。
        """
        for run_id in self.repository.reap_stale_runs(
            max_age=timedelta(hours=self.config.stale_run_max_age_hours)
        ):
            logger.warning("回收超时未收尾的 run %s,判 FAILED", run_id)

        targets = self.repository.list_queue_b_targets(unreferenced_only=True)
        throttle = self._make_throttle()
        # 全量页数事先不知道(大盘小区多页)。硬顶已经在 full_sweep_config 里抬到
        # 5000,这里不再按「小区数 × 1.15」收紧——那会在扫到一半时假穷尽。
        logger.info(
            "队列 B 全量开始:待刷新 %s 个小区,当日已花 %s / 硬顶 %s",
            len(targets), throttle.spent_today(), throttle.daily_budget,
        )
        results: list[QueueBOutcome] = []
        stopped: str | None = None
        started = self._clock()
        for index, community in enumerate(targets, start=1):
            try:
                if self.enforce_window:
                    self._require_window_open()
                outcome = self._run_one_community(community, throttle)
            except ThrottleStop as exc:
                stopped = exc.reason
                logger.warning("队列 B 全量中止(%s),已完成 %s/%s",
                               exc.reason, _count_success(results), len(targets))
                break
            results.append(outcome)
            if outcome.status == ABORTED:
                stopped = outcome.reason
                break
            elapsed = (self._clock() - started).total_seconds()
            remaining = len(targets) - index
            eta = (elapsed / index * remaining) if index else 0.0
            logger.info(
                "队列 B 全量 %s/%s %s %s (%s) 页 %s 行 %s 剩余约 %.1fh",
                index, len(targets), outcome.status, community["id"],
                community["name"], outcome.pages_done, outcome.reference_rows,
                eta / 3600.0,
            )
        return _sweep_outcome(targets, results, stopped)

    def _run_group(self, targets: Sequence[Mapping[str, object]]) -> QueueBOutcome:
        """多小区共用一个 run。日常配额走这里;全量不走(避免 12 小时事务)。"""
        throttle = self._make_throttle()
        run_id = self.repository.start_run(QUEUE_B)
        logger.info("队列 B run %s 开始,%s 个小区,当日已花 %s 次",
                    run_id, len(targets), throttle.spent_today())
        pacing = _Pacing(self.config, self._rng)
        retries_left = self.config.retry_reserve
        pages_done = 0
        pages_expected = 0
        outcomes: dict[str, str] = {}
        first_id = str(targets[0]["id"])
        first_name = str(targets[0]["name"])
        try:
            for community in targets:
                if self.enforce_window:
                    self._require_window_open()
                result = self._crawl_community(
                    run_id, community, throttle, retries_left, pacing,
                )
                retries_left = int(result["retries_left"])
                pages_done += int(result["pages_done"])
                pages_expected += int(result["pages_expected"])
                outcomes[str(community["id"])] = str(result["status"])
                self.repository.record_progress(
                    run_id, pages_done=pages_done, pages_expected=pages_expected,
                    upstream_total=result["total"],
                )
            return self._finish_group(run_id, outcomes, pages_done, pages_expected,
                                      first_id, first_name)
        except ThrottleStop as exc:
            if any(status == COMMUNITY_SUCCESS for status in outcomes.values()):
                self.repository.record_progress(
                    run_id, pages_done=pages_done, pages_expected=pages_done,
                )
                return self._finish_group(
                    run_id, outcomes, pages_done, pages_done, first_id, first_name,
                    extra_reason=exc.reason,
                )
            self.repository.abort_run(run_id, exc.reason)
            requests = self.repository.sync_run_request_count(run_id)
            logger.warning("队列 B run %s 中止(%s)", run_id, exc.reason)
            return QueueBOutcome(
                run_id=run_id, status=ABORTED, community_id=first_id,
                community_name=first_name, pages_done=pages_done,
                pages_expected=pages_expected, requests=requests, reason=exc.reason,
                community_outcomes=outcomes,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            self.repository.fail_run(run_id, reason)
            requests = self.repository.sync_run_request_count(run_id)
            logger.exception("队列 B run %s 失败", run_id)
            return QueueBOutcome(
                run_id=run_id, status=FAILED, community_id=first_id,
                community_name=first_name, pages_done=pages_done,
                pages_expected=pages_expected, requests=requests, reason=reason,
                community_outcomes=outcomes,
            )

    def _run_one_community(
        self, community: Mapping[str, object], throttle: RoughcastThrottle,
    ) -> QueueBOutcome:
        community_id = str(community["id"])
        name = str(community["name"])
        run_id = self.repository.start_run(QUEUE_B)
        pacing = _Pacing(self.config, self._rng)
        try:
            if self.enforce_window:
                self._require_window_open()
            result = self._crawl_community(
                run_id, community, throttle, self.config.retry_reserve, pacing,
            )
            pages_done = int(result["pages_done"])
            pages_expected = int(result["pages_expected"])
            self.repository.record_progress(
                run_id, pages_done=pages_done, pages_expected=pages_expected,
                upstream_total=result["total"],
            )
            status = str(result["status"])
            if status == COMMUNITY_SUCCESS:
                published = self.repository.complete_queue_b_run(
                    run_id, community_outcomes={community_id: COMMUNITY_SUCCESS},
                )
                requests = self.repository.sync_run_request_count(run_id)
                return QueueBOutcome(
                    run_id=run_id, status=COMPLETE, community_id=community_id,
                    community_name=name, pages_done=pages_done,
                    pages_expected=pages_expected, requests=requests,
                    reference_rows=int(published["reference_rows"]),
                    reference_r_rows=int(published["reference_r_rows"]),
                    community_outcomes={community_id: COMMUNITY_SUCCESS},
                )
            self.repository.mark_community_refresh_failed(community_id)
            self.repository.fail_run(run_id, str(result["reason"]))
            requests = self.repository.sync_run_request_count(run_id)
            return QueueBOutcome(
                run_id=run_id, status=FAILED, community_id=community_id,
                community_name=name, pages_done=pages_done,
                pages_expected=pages_expected, requests=requests,
                reason=str(result["reason"]),
                community_outcomes={community_id: status},
            )
        except ThrottleStop as exc:
            self.repository.abort_run(run_id, exc.reason)
            requests = self.repository.sync_run_request_count(run_id)
            run = self.repository.get_run(run_id)
            logger.warning("队列 B 小区 %s 中止(%s)", community_id, exc.reason)
            return QueueBOutcome(
                run_id=run_id, status=ABORTED, community_id=community_id,
                community_name=name,
                pages_done=int(run["pages_done"]) if run else 0,
                pages_expected=run["pages_expected"] if run else None,
                requests=requests, reason=exc.reason,
            )
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            self.repository.fail_run(run_id, reason)
            self.repository.mark_community_refresh_failed(community_id)
            requests = self.repository.sync_run_request_count(run_id)
            logger.exception("队列 B 小区 %s 失败", community_id)
            return QueueBOutcome(
                run_id=run_id, status=FAILED, community_id=community_id,
                community_name=name, pages_done=0, pages_expected=None,
                requests=requests, reason=reason,
            )

    def _finish_group(
        self, run_id: int, outcomes: Mapping[str, str],
        pages_done: int, pages_expected: int,
        first_id: str, first_name: str, *, extra_reason: str | None = None,
    ) -> QueueBOutcome:
        try:
            published = self.repository.complete_queue_b_run(
                run_id, community_outcomes=outcomes,
            )
        except RunStateError as exc:
            self.repository.fail_run(run_id, str(exc))
            requests = self.repository.sync_run_request_count(run_id)
            return QueueBOutcome(
                run_id=run_id, status=FAILED, community_id=first_id,
                community_name=first_name, pages_done=pages_done,
                pages_expected=pages_expected, requests=requests, reason=str(exc),
                community_outcomes=outcomes,
            )
        requests = self.repository.sync_run_request_count(run_id)
        run = self.repository.get_run(run_id)
        terminal = str(run["status"]) if run else COMPLETE
        reason = extra_reason or (str(run["abort_reason"]) if run and run["abort_reason"] else None)
        return QueueBOutcome(
            run_id=run_id, status=terminal, community_id=first_id,
            community_name=first_name, pages_done=pages_done,
            pages_expected=pages_expected, requests=requests,
            reference_rows=int(published["reference_rows"]),
            reference_r_rows=int(published["reference_r_rows"]),
            reason=reason, community_outcomes=outcomes,
        )

    def _crawl_community(
        self, run_id: int, community: Mapping[str, object],
        throttle: RoughcastThrottle, retries_left: int, pacing: _Pacing,
    ) -> dict[str, object]:
        community_id = str(community["id"])
        resblock_id = str(community["resblock_id"])
        log_id, page1 = self._fetch_page_with_retry(
            run_id, throttle, resblock_id=resblock_id, community_id=community_id,
            page=1, retries_left=retries_left,
        )
        retries_left = max(retries_left - 1, 0)
        self._stage(run_id, log_id, page1, community_id)
        total = page1.total
        if total >= COMMUNITY_TOTAL_HARD_CAP:
            logger.warning(
                "run %s 小区 %s totalCount=%s 过 cap,不发布",
                run_id, community_id, total,
            )
            return {
                "status": COMMUNITY_OVER_CAP,
                "reason": f"totalCount={total} >= {COMMUNITY_TOTAL_HARD_CAP}",
                "total": total,
                "pages_expected": 1,
                "pages_done": 1,
                "retries_left": retries_left,
            }
        pages_expected = self._pages_from_total(total) or 1
        pages_done = 1
        if not page1.has_more:
            return {
                "status": COMMUNITY_SUCCESS,
                "reason": None,
                "total": total,
                "pages_expected": 1,
                "pages_done": 1,
                "retries_left": retries_left,
            }
        page = 2
        while page <= pages_expected:
            if self.enforce_window:
                self._require_window_open()
            try:
                log_id, crawl_page = self._fetch_page_with_retry(
                    run_id, throttle, resblock_id=resblock_id, community_id=community_id,
                    page=page, retries_left=retries_left,
                )
            except CrawlRequestError as exc:
                logger.warning("run %s 小区 %s 第 %s 页失败(%s)",
                               run_id, community_id, page, exc)
                return {
                    "status": COMMUNITY_FAILED,
                    "reason": f"page {page} failed: {exc}",
                    "total": total,
                    "pages_expected": pages_done,
                    "pages_done": pages_done,
                    "retries_left": retries_left,
                }
            self._stage(run_id, log_id, crawl_page, community_id)
            pages_done = page
            if not crawl_page.has_more:
                pages_expected = pages_done
                break
            if page >= pages_expected:
                logger.info(
                    "run %s 小区 %s 翻满 %s 页 has_more 仍 true,本轮不追尾巴",
                    run_id, community_id, pages_expected,
                )
                break
            gap = pacing.next_gap_seconds()
            if gap > 0:
                self._sleeper(gap)
            page += 1
        return {
            "status": COMMUNITY_SUCCESS,
            "reason": None,
            "total": total,
            "pages_expected": pages_expected,
            "pages_done": pages_done,
            "retries_left": retries_left,
        }

    def _stage(self, run_id: int, log_id: int, crawl_page: CrawlPage,
               community_id: str) -> None:
        inserted, returned = self.repository.stage_rows(
            run_id, crawl_page.rows, bucket=queue_b_bucket(community_id),
        )
        self.repository.update_request_log_counters(
            log_id, rows_returned=returned, rows_new=inserted,
        )

    def _fetch_page_with_retry(
        self, run_id: int, throttle: RoughcastThrottle, *,
        resblock_id: str, community_id: str, page: int, retries_left: int,
    ) -> tuple[int, CrawlPage]:
        while True:
            target = f"resblock={community_id}&page={page}"
            log_id = throttle.acquire(queue=QUEUE_B, target=target, run_id=run_id)
            try:
                crawl_page = self.client.search_community_page(resblock_id, page)
            except CrawlRequestError as exc:
                throttle.failed(
                    log_id, http_status=exc.http_status, error_code=exc.error_code,
                    note=str(exc), queue=QUEUE_B, run_id=run_id,
                )
                if retries_left <= 0:
                    raise
                retries_left -= 1
                logger.warning(
                    "run %s 小区 %s 第 %s 页失败(%s),剩余重试 %s 次",
                    run_id, community_id, page, exc, retries_left,
                )
                gap = self._rng.uniform(*self.config.page_gap_seconds)
                if gap > 0:
                    self._sleeper(gap)
                if self.enforce_window:
                    self._require_window_open()
                continue
            throttle.succeeded(log_id)
            return log_id, crawl_page

    def _pages_from_total(self, total: int) -> int | None:
        if total <= 0:
            return None
        return -(-total // self.client.page_size)

    def _local_now(self) -> datetime:
        return self._clock().astimezone(UTC) + self.config.timezone

    def _within_window(self, moment: datetime) -> bool:
        return self.config.window_start <= moment.time() <= self.config.window_end

    def _require_window_open(self) -> None:
        if not self._within_window(self._local_now()):
            raise WindowClosed(
                f"已超出 {self.config.window_start}–{self.config.window_end} 采集窗口"
            )


def full_sweep_config(base: RoughcastCrawlConfig) -> RoughcastCrawlConfig:
    """第一次全量:抬硬顶、关掉长停顿、窗口交给 crawler.enforce_window=False。

    最小 20 秒间隔保留——这是第三章的硬规则,全量也按这个节奏。
    """
    return RoughcastCrawlConfig(
        page_size=base.page_size,
        daily_request_cap=max(base.daily_request_cap, FULL_SWEEP_REQUEST_CAP),
        safety_factor=base.safety_factor,
        retry_reserve=base.retry_reserve,
        min_request_interval_seconds=base.min_request_interval_seconds,
        page_gap_seconds=(0.0, 0.0),
        long_pause_every_pages=(10**9, 10**9),
        long_pause_seconds=(0.0, 0.0),
        window_start=dtime(0, 0),
        window_end=dtime(23, 59, 59),
        start_jitter_minutes=0,
        utc_offset_hours=base.utc_offset_hours,
        stale_run_max_age_hours=base.stale_run_max_age_hours,
        bucket_plan=base.bucket_plan,
    )


def build_queue_b_crawler(
    settings: Settings, database: Database, *,
    full_sweep: bool = False,
) -> RoughcastQueueBCrawler:
    base = crawl_config_from_settings(settings)
    config = full_sweep_config(base) if full_sweep else base
    return RoughcastQueueBCrawler(
        RoughcastRepository(database),
        RoughcastCrawlClient(settings.crm_connector_base_url, page_size=config.page_size),
        config,
        enforce_window=not full_sweep,
    )


def _count_success(results: Sequence[QueueBOutcome]) -> int:
    return sum(1 for item in results if item.status == COMPLETE)


def _sweep_outcome(
    targets: Sequence[Mapping[str, object]],
    results: Sequence[QueueBOutcome],
    stopped: str | None,
) -> SweepOutcome:
    return SweepOutcome(
        communities_targeted=len(targets),
        communities_success=sum(1 for item in results if item.status == COMPLETE),
        communities_failed=sum(1 for item in results if item.status == FAILED),
        communities_aborted=sum(1 for item in results if item.status == ABORTED),
        pages_done=sum(item.pages_done for item in results),
        requests=sum(item.requests for item in results),
        reference_rows=sum(item.reference_rows for item in results),
        reference_r_rows=sum(item.reference_r_rows for item in results),
        stopped_reason=stopped,
        results=tuple(results),
    )
