"""队列 A:清水房全量采集（`docs/roughcast-quality-ranking.md` §三 / §4.2）。

**只采集,不评分**（第 1 期）。一轮 = 一个 `roughcast_crawl_runs` 行,
终态有四种,`COMPLETE` 与 `PARTIAL` 都会发布(4.2 规则 1):

    RUNNING ──11 档全成功──▶ COMPLETE   （全部 stage 行进 listing_*）
       ├──11 档部分失败────▶ PARTIAL    （只发成功档的 stage 行）
       ├──熔断 / 预算耗尽 / 窗口关闭──▶ ABORTED
       └──异常 / 进程退出─────────────▶ FAILED

V2.5 起支持 11 档切分:每次 search 加 `price="lo:hi"`,把单次查询的
`totalCount` 压到 < 1000。**单档失败不影响其他档**——这是 V2.4 漏
70% 数据的关键修法,见 [[roughcast-deep-paging-cap]]。

时钟、`sleep`、随机数全部注入,所以单测零等待且可复现。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time as dtime, timedelta
from typing import Callable, Mapping

from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import (
    CrawlPage,
    CrawlRequestError,
    RoughcastCrawlClient,
    format_price_range,
)
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMPLETE,
    FAILED,
    PARTIAL,
    RoughcastRepository,
    RunStateError,
)
from app.infrastructure.roughcast_throttle import (
    RoughcastThrottle,
    ThrottleStop,
    plan_budget,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

QUEUE_A = "A"

# 单档 totalCount 硬顶。超过即该档 PARTIAL(只发 page 1 的 50 行)。
# 与 [[roughcast-deep-paging-cap]] 的探针结论一致:任何「过 1000」就意味
# 着用单查询不可能拿全,必须二分或弃档。
BUCKET_TOTAL_HARD_CAP = 1000

# 档状态枚举。给 `bucket_outcomes` 用,与 repository 里的判定一致。
BUCKET_STATUS_SUCCESS = "success"
BUCKET_STATUS_OVER_CAP = "skipped_over_cap"
BUCKET_STATUS_FAILED = "bucket_failed"


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
    # V2.5:11 档切分。`((None, None),)` 是单档(全价)兼容旧路径。
    # `None` 表示开区间:lo 走 `"0"`,hi 走 `"-1"`,见 `format_price_range`。
    bucket_plan: tuple[tuple[int | None, int | None], ...] = ((None, None),)

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
    # V2.5:11 档切分后每档的判定。键是 bucket 标签 (如 '0:800'),值是
    # 'success' / 'skipped_over_cap' / 'breaker' / 等。PARTIAL 时失败档
    # 在这里完整可见,CrawlOutcome 的 status 字段只是收尾状态。
    bucket_outcomes: Mapping[str, str] | None = None
    # V2.5:本轮发布的房源里,score_source 三值计数。给 `--status` 报告用。
    score_source_counts: Mapping[str, int] | None = None


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
        run_id = self.repository.start_run(QUEUE_A, planned_buckets=self.config.bucket_plan)
        logger.info("队列 A run %s 开始,%s 档,当日已花 %s 次",
                    run_id, len(self.config.bucket_plan), throttle.spent_today())
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
        """V2.5:11 档编排。

        每档流程:
        1. 发 page=1 拿 `totalCount`;若 ≥ 1000 直接 `skipped_over_cap` 跳到下档
        2. 否则按 `_pages_from_total(total)` 翻到 `has_more=false`
        3. 档内任意 page 抛 `CrawlRequestError`(重试耗尽)→ 该档
           `bucket_failed`,记原因,**继续下档**(单档失败不应击穿整轮)
        4. `ThrottleStop`(熔断/预算)→ 抛给外层,整轮 ABORTED

        跑完全部档后调 `complete_run(bucket_outcomes=...)`:
        - 全 success → COMPLETE
        - 部分 success → PARTIAL,只发成功档
        - 全失败 → RunStateError,外层判 FAILED(0 档成功不该发布)
        """
        pacing = _Pacing(self.config, self._rng)
        retries_left = self.config.retry_reserve
        pages_done = 0
        pages_expected_total = 0
        first_total: int | None = None
        bucket_outcomes: dict[str, str] = {}
        bucket_details: dict[str, dict[str, object]] = {}

        for lo, hi in self.config.bucket_plan:
            price_range = format_price_range(lo, hi)
            self._require_window_open()
            outcome = self._crawl_one_bucket(
                run_id, throttle, price_range, retries_left, pacing,
            )
            retries_left = int(outcome["retries_left"])
            pages_done += int(outcome["pages_done_in_bucket"])
            if first_total is None:
                first_total = outcome["total_count"]
            pages_expected_total += int(outcome["pages_expected_in_bucket"])
            bucket_outcomes[price_range] = str(outcome["status"])
            bucket_details[price_range] = {
                "total": outcome["total_count"],
                "pages": int(outcome["pages_expected_in_bucket"]),
                "rows_staged": int(outcome["rows_staged"]),
                "reason": outcome["reason"],
            }
            self.repository.record_bucket_outcomes(run_id, {
                price_range: str(outcome["status"]),
            })

        # 第一档的 total 当 upstream_total(历史上 `total` 列是这么用的)。
        # 真要看 11 档各自的 total,查 `bucket_outcomes` JSON 即可。
        self.repository.record_progress(
            run_id, pages_done=pages_done, pages_expected=pages_expected_total,
            upstream_total=first_total,
        )
        self._tighten_budget(throttle, pages_expected_total)

        try:
            published = self.repository.complete_run(
                run_id,
                bucket_outcomes=bucket_outcomes,
                bucket_details=bucket_details,
            )
        except RunStateError as exc:
            # 0 档成功的情况——走 FAILED,外层 handler 收尾。
            self.repository.fail_run(run_id, str(exc))
            requests = self.repository.sync_run_request_count(run_id)
            return CrawlOutcome(
                run_id=run_id, status=FAILED, pages_done=pages_done,
                pages_expected=pages_expected_total, requests=requests, reason=str(exc),
                bucket_outcomes=bucket_outcomes,
            )
        requests = self.repository.sync_run_request_count(run_id)
        run = self.repository.get_run(run_id)
        terminal_status = str(run["status"]) if run else COMPLETE
        published_count = int(published["published"])
        snapshots = int(published["snapshots_inserted"])
        deactivated = int(published["deactivated"])
        logger.info(
            "run %s %s:%s 档 / %s 页 / %s 次请求 / 发布 %s 套 / 新快照 %s 行 / 下架 %s 套",
            run_id, terminal_status, len(self.config.bucket_plan), pages_done, requests,
            published_count, snapshots, deactivated,
        )
        return CrawlOutcome(
            run_id=run_id, status=terminal_status, pages_done=pages_done,
            pages_expected=pages_expected_total, requests=requests,
            bucket_outcomes=bucket_outcomes,
            score_source_counts=published.get("score_source_counts"),
            **{
                k: v for k, v in published.items()
                if k in ("published", "snapshots_inserted", "deactivated")
            },
        )

    def _crawl_one_bucket(
        self, run_id: int, throttle: RoughcastThrottle, price_range: str,
        retries_left: int, pacing: _Pacing,
    ) -> dict[str, object]:
        """跑一档。档级失败不抛——抛回 `_crawl` 只会让上游的熔断逻辑误判;
        档级失败应只标该档的 status。`ThrottleStop` 是档间共享资源(预算/熔断)
        的失败,会向上抛让外层走 ABORTED。"""
        _, page1 = self._fetch_page_with_retry(
            run_id, throttle, page=1, price_range=price_range, retries_left=retries_left,
        )
        # page 1 已用 1 次重试配额;剩余预算从 max(retries_left - 1, 0) 算起。
        retries_left = max(retries_left - 1, 0)
        total = page1.total
        if total >= BUCKET_TOTAL_HARD_CAP:
            logger.warning(
                "run %s 档 %s totalCount=%s 过 cap,该档不发布",
                run_id, price_range, total,
            )
            self._stage_with_counters(run_id, page1, price_range)
            # 把已发出的 page 1 记进去,ABORTED 路径要从数据库读到进度。
            # `pages_expected_in_bucket=1` 是「我们就计划发 1 页」——和
            # `pages_done_in_bucket=1` 对齐,complete_run 不会因页数不一致拒绝。
            self.repository.record_progress(
                run_id, pages_done=1, pages_expected=1, upstream_total=total,
            )
            return {
                "status": BUCKET_STATUS_OVER_CAP,
                "reason": f"totalCount={total} >= {BUCKET_TOTAL_HARD_CAP}",
                "total_count": total,
                "pages_expected_in_bucket": 1,
                "pages_done_in_bucket": 1,
                "rows_staged": 0,
                "retries_left": retries_left,
            }
        pages_expected = self._pages_from_total(total) or 0
        rows_staged = 0
        pages_done_in_bucket = 0
        rows_staged += self._stage_with_counters(run_id, page1, price_range)
        pages_done_in_bucket = 1
        # 每页落库 `pages_done`,保证 `ThrottleStop` 触发的 ABORTED 路径
        # 能从 `run` 表读到当前进度(V2.4 行为,不能丢)。
        self.repository.record_progress(
            run_id, pages_done=1, pages_expected=pages_expected, upstream_total=total,
        )
        page = 2
        while pages_expected == 0 or page <= pages_expected:
            self._require_window_open()
            try:
                _, crawl_page = self._fetch_page_with_retry(
                    run_id, throttle, page=page, price_range=price_range,
                    retries_left=retries_left,
                )
            except CrawlRequestError as exc:
                logger.warning(
                    "run %s 档 %s 第 %s 页失败(%s),该档不再继续",
                    run_id, price_range, page, exc,
                )
                # 把 expected 拉到 done,与 has_more=false 走同样规则——
                # complete_run 的「页数一致」判定不应被档级失败击穿;失败
                # 状态由 `bucket_outcomes[bucket]='bucket_failed'` 表达。
                if pages_expected and pages_done_in_bucket != pages_expected:
                    pages_expected = pages_done_in_bucket
                return {
                    "status": BUCKET_STATUS_FAILED,
                    "reason": f"page {page} failed: {exc}",
                    "total_count": total,
                    "pages_expected_in_bucket": pages_expected,
                    "pages_done_in_bucket": pages_done_in_bucket,
                    "rows_staged": rows_staged,
                    "retries_left": retries_left,
                }
            rows_staged += self._stage_with_counters(run_id, crawl_page, price_range)
            pages_done_in_bucket = page
            self.repository.record_progress(
                run_id, pages_done=pages_done_in_bucket,
            )
            if not crawl_page.has_more:
                # V2.4 行为:total 跨页波动是常态,把 expected 拉到 done
                # 再交付,避免「页数不一致」触发 complete_run 拒绝。
                if pages_expected and pages_done_in_bucket != pages_expected:
                    logger.info(
                        "run %s 档 %s 第 %s 页 has_more=false,实际 %s < 预期 %s;以 has_more 为准",
                        run_id, price_range, page, pages_done_in_bucket, pages_expected,
                    )
                    pages_expected = pages_done_in_bucket
                break
            if page >= pages_expected and pages_expected:
                logger.info(
                    "run %s 档 %s 翻满 %s 页 has_more 仍 true,total 在涨,本轮不追尾巴",
                    run_id, price_range, pages_expected,
                )
                break
            self._sleeper(pacing.next_gap_seconds())
            page += 1
        return {
            "status": BUCKET_STATUS_SUCCESS,
            "reason": None,
            "total_count": total,
            "pages_expected_in_bucket": pages_expected or pages_done_in_bucket,
            "pages_done_in_bucket": pages_done_in_bucket,
            "rows_staged": rows_staged,
            "retries_left": retries_left,
        }

    def _stage_with_counters(self, run_id: int, crawl_page: CrawlPage,
                             bucket_label: str) -> int:
        """落 stage + 回填 `crawl_log.rows_returned / rows_new`。

        这是 [[roughcast-deep-paging-cap]] 提到的覆盖度审计的入口:
        每一行 `crawl_log` 都带这两列,「rows_new=0 且 rows_returned>0」
        立刻能 SQL 查出来。
        """
        inserted, returned = self.repository.stage_rows(
            run_id, crawl_page.rows, bucket=bucket_label,
        )
        with self.repository.database.connect() as db:
            row = db.execute(
                "SELECT id FROM roughcast_crawl_log "
                "WHERE run_id = ? AND target LIKE ? AND status = 'ok' "
                "AND rows_returned = 0 AND rows_new = 0 "
                "ORDER BY id DESC LIMIT 1",
                (run_id, f"%bucket={bucket_label}&page={crawl_page.page}"),
            ).fetchone()
        if row is not None:
            self.repository.update_request_log_counters(
                int(row["id"]), rows_returned=returned, rows_new=inserted,
            )
        return inserted

    def _fetch_page_with_retry(
        self, run_id: int, throttle: RoughcastThrottle, *,
        page: int, price_range: str, retries_left: int,
    ) -> tuple[int, CrawlPage]:
        """取一页,失败按 `retries_left` 走重试;耗尽抛 `CrawlRequestError`。

        成功返回 `(log_id, page)`;`log_id` 给 `_stage_with_counters` 用来
        回填 rows_returned/rows_new。`ThrottleStop` 留给外层做 ABORTED。
        """
        while True:
            target = f"bucket={price_range}&page={page}"
            log_id = throttle.acquire(queue=QUEUE_A, target=target, run_id=run_id)
            try:
                crawl_page = self.client.search_page(page, price_range=price_range)
            except CrawlRequestError as exc:
                throttle.failed(
                    log_id, http_status=exc.http_status, error_code=exc.error_code,
                    note=str(exc), queue=QUEUE_A, run_id=run_id,
                )
                if retries_left <= 0:
                    raise
                retries_left -= 1
                logger.warning(
                    "run %s 档 %s 第 %s 页失败(%s),剩余重试 %s 次",
                    run_id, price_range, page, exc, retries_left,
                )
                self._sleeper(self._rng.uniform(*self.config.page_gap_seconds))
                self._require_window_open()
                continue
            throttle.succeeded(log_id)
            return log_id, crawl_page

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
        bucket_plan=settings.roughcast_bucket_plan,
    )


def build_crawler(settings: Settings, database: Database) -> RoughcastCrawler:
    config = crawl_config_from_settings(settings)
    return RoughcastCrawler(
        RoughcastRepository(database),
        RoughcastCrawlClient(settings.crm_connector_base_url, page_size=config.page_size),
        config,
    )
