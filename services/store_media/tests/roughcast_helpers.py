"""第 1 期采集链路的共用测试脚手架。

时钟 / sleep / 随机数全部注入,所以这些测试零等待、可复现,并且**不发任何
真实上游请求**——`FakeCrawlClient` 是唯一的数据来源。
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.application.roughcast_crawler import RoughcastCrawlConfig, RoughcastCrawler
from app.domain.roughcast import RoughcastRow, community_key
from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import CrawlPage, CrawlRequestError
from app.infrastructure.roughcast_repository import RoughcastRepository

BASE_MOMENT = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)   # 本地 11:00,窗口内


def make_db(tmp_path) -> Database:
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    return database


def make_row(listing_id: str, **overrides) -> RoughcastRow:
    defaults = {
        "community_name": "示例小区",
        "resblock_id": "RB001",
        "layout": "2室1厅1卫",
        "rooms": 2,
        "halls": 1,
        "baths": 1,
        "area_sqm": 88.5,
        "monthly_rent_yuan": 4300.0,
        "orientation": "南",
        "floor_desc": "中楼层",
        "total_floors": 18,
        "rent_mode": "整租",
        "del_type": 1,
        "fitment_status": "002",
        "fitment_status_desc": "毛坯",
        "create_time": "2026-06-01T00:00:00+00:00",
        "title_image_url": "https://img.example.com/a.jpg",
    }
    # V2.5 现实主义:测试数据有 `resblock_id` 时,默认 `community_lookup_status=found`
    # 与生产 `_row_from_response` 一致。显式传 `community_lookup_status=...`
    # 仍可覆盖。
    if "community_lookup_status" not in overrides:
        defaults["community_lookup_status"] = "found" if defaults["resblock_id"] else "not_found"
    row = RoughcastRow(listing_id=listing_id, **{**defaults, **overrides})
    return replace(row, community_id=community_key(row))


class FakeClock:
    """可推进的时钟。测试要控制「当天」与「窗口」,所以必须显式推进。"""

    def __init__(self, start: datetime = BASE_MOMENT):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeMonotonic:
    """单调钟。`sleep` 会把它推进,于是最小间隔的等待时长可被断言。"""

    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class RecordingSleeper:
    def __init__(self, monotonic: FakeMonotonic | None = None):
        self.calls: list[float] = []
        self._monotonic = monotonic

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._monotonic is not None:
            self._monotonic.value += seconds


class FakeCrawlClient:
    """按页返回预置数据。所有 HTTP 动作都记进 `requests`,便于断言零详情请求。

    V2.5:支持 `price_range` 参数(透传给 `requests` 列表的 target 字段,
    方便测试断言「第 1 档用 0:800、第 2 档用 800:1200」之类的预期)。
    多个 `(page, price_range)` 组合按页号 + 价格区段联合索引,这样
    11 档切分的测试可以同时预置所有档的 page 1。
    """

    def __init__(self, pages: list[CrawlPage] | None = None, *,
                 errors: dict[int, list[CrawlRequestError]] | None = None,
                 community_pages: list[tuple[str, CrawlPage]] | None = None,
                 community_errors: dict[tuple[str, int], list[CrawlRequestError]] | None = None,
                 page_size: int = 50):
        # V2.5:键是 `(page, price_range)`。`page_of` 接收 `price_range=`
        # 时会写进 `CrawlPage.price_range`,这里读出来;没传时是 None,
        # 与 V2.4 单查询路径一致——把同一批页既给单档用、也允许其他
        # 档按 `price_range=` 预置不同页,`--status` 那批 11 档测试就用得上。
        self._pages: dict[tuple[int, str | None], CrawlPage] = {}
        for page in pages or []:
            key = (page.page, page.price_range)
            self._pages[key] = page
        self._community_pages: dict[tuple[str, int], CrawlPage] = {}
        for resblock_id, page in community_pages or []:
            self._community_pages[(resblock_id, page.page)] = page
        self._errors = {key: list(value) for key, value in (errors or {}).items()}
        self._community_errors = {
            key: list(value) for key, value in (community_errors or {}).items()
        }
        self._page_size = page_size
        self.requests: list[tuple[str, str]] = []      # (method, target)
        self.search_bodies: list[dict[str, object]] = []

    @property
    def page_size(self) -> int:
        return self._page_size

    def search_page(self, page: int, *, price_range: str | None = None) -> CrawlPage:
        target = f"bucket={price_range}&page={page}" if price_range else f"page={page}"
        self.requests.append(("POST", f"search:{target}"))
        pending = self._errors.get(page)
        if pending:
            raise pending.pop(0)
        # 先按精确 `(page, price_range)` 查;V2.5 11 档测试会预置带
        # `price_range=` 的页。找不到时回退到 `(page, None)`——这是 V2.4
        # 单查询路径,老测试和「同一批页给所有档共用」的场景都靠这条。
        key = (page, price_range)
        if key in self._pages:
            return self._pages[key]
        fallback = (page, None)
        if fallback in self._pages:
            return self._pages[fallback]
        raise AssertionError(f"测试未预置 {target}")

    def search_community_page(self, resblock_id: str, page: int) -> CrawlPage:
        target = f"resblock={resblock_id}&page={page}"
        self.requests.append(("POST", f"search:{target}"))
        self.search_bodies.append({
            "scope": "all",
            "resblock_ids": [resblock_id],
            "page": page,
            "page_size": self._page_size,
        })
        pending = self._community_errors.get((resblock_id, page))
        if pending:
            raise pending.pop(0)
        key = (resblock_id, page)
        if key in self._community_pages:
            return self._community_pages[key]
        raise AssertionError(f"测试未预置 {target}")


def page_of(rows, page: int, total: int, has_more: bool, *,
            price_range: str | None = None) -> CrawlPage:
    """V2.5:把 `price_range` 透传到 `CrawlPage`,让 FakeCrawlClient
    能按 `(page, price_range)` 联合索引,11 档测试可同时预置各档 page 1。"""
    return CrawlPage(
        rows=tuple(rows), page=page, total=total, has_more=has_more,
        price_range=price_range,
    )


def seed_community(database: Database, *, community_id: str, name: str = "示例小区",
                   resblock_id: str | None = None, roughcast_count: int = 1,
                   bizcircle: str | None = "华侨城",
                   reference_run_id: int | None = None) -> None:
    """队列 B 测试用:直接写入小区档案,不跑队列 A。"""
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_communities "
            "(id, name, resblock_id, bizcircle, roughcast_count, "
            " reference_run_id, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (community_id, name, resblock_id if resblock_id is not None else community_id,
             bizcircle, roughcast_count, reference_run_id, "2026-08-20T03:00:00+00:00"),
        )


def make_queue_b_crawler(database: Database, client, *, clock: FakeClock | None = None,
                         config: RoughcastCrawlConfig | None = None,
                         monotonic: FakeMonotonic | None = None,
                         sleeper: RecordingSleeper | None = None,
                         seed: int = 1234, enforce_window: bool = True):
    from app.application.roughcast_queue_b import RoughcastQueueBCrawler

    resolved_clock = clock or FakeClock()
    resolved_monotonic = monotonic or FakeMonotonic()
    resolved_sleeper = sleeper or RecordingSleeper(resolved_monotonic)
    resolved_config = config or RoughcastCrawlConfig(
        page_size=client.page_size,
        page_gap_seconds=(0.0, 0.0),
        long_pause_seconds=(0.0, 0.0),
        min_request_interval_seconds=0.0,
    )
    return RoughcastQueueBCrawler(
        RoughcastRepository(database, clock=resolved_clock),
        client,
        resolved_config,
        clock=resolved_clock,
        monotonic=resolved_monotonic,
        sleeper=resolved_sleeper,
        rng=random.Random(seed),
        enforce_window=enforce_window,
    )


def make_crawler(database: Database, client, *, clock: FakeClock | None = None,
                 config: RoughcastCrawlConfig | None = None,
                 monotonic: FakeMonotonic | None = None,
                 sleeper: RecordingSleeper | None = None,
                 seed: int = 1234) -> RoughcastCrawler:
    resolved_clock = clock or FakeClock()
    resolved_monotonic = monotonic or FakeMonotonic()
    resolved_sleeper = sleeper or RecordingSleeper(resolved_monotonic)
    return RoughcastCrawler(
        RoughcastRepository(database, clock=resolved_clock),
        client,
        config or RoughcastCrawlConfig(page_size=client.page_size),
        clock=resolved_clock,
        monotonic=resolved_monotonic,
        sleeper=resolved_sleeper,
        rng=random.Random(seed),
    )


def make_loop(database: Database, queue_a_client, queue_b_client, *,
              clock: FakeClock | None = None,
              queue_b_daily_limit: int = 60,
              score_after_crawl: bool = True,
              storage_dir=None,
              seed: int = 1234):
    """组装一个 `RoughcastDailyLoop`,A/B 共用一个 `FakeClock`。

    不起线程——测试直接调 `run_tick()`。`storage_dir` 默认用
    `database.path.parent`,传 `tmp_path` 也行,只是 Settings 需要它存在。
    """
    from app.application.roughcast_loop import RoughcastDailyLoop
    from app.application.roughcast_queue_b import RoughcastQueueBCrawler
    from app.infrastructure.settings import Settings

    resolved_clock = clock or FakeClock()
    if storage_dir is None:
        storage_dir = database.path.parent
    settings = Settings(
        storage_dir=storage_dir,
        roughcast_score_after_crawl=score_after_crawl,
        roughcast_queue_b_daily_limit=queue_b_daily_limit,
    )
    repository = RoughcastRepository(database, clock=resolved_clock)
    queue_a_config = RoughcastCrawlConfig(
        page_size=queue_a_client.page_size,
        page_gap_seconds=(0.0, 0.0),
        long_pause_seconds=(0.0, 0.0),
        long_pause_every_pages=(10**9, 10**9),
        min_request_interval_seconds=0.0,
    )
    queue_a = RoughcastCrawler(
        repository, queue_a_client, queue_a_config,
        clock=resolved_clock, monotonic=FakeMonotonic(),
        sleeper=RecordingSleeper(), rng=random.Random(seed),
    )
    queue_b = RoughcastQueueBCrawler(
        repository, queue_b_client, queue_a_config,
        clock=resolved_clock, monotonic=FakeMonotonic(),
        sleeper=RecordingSleeper(), rng=random.Random(seed),
        enforce_window=True,
    )
    return RoughcastDailyLoop(
        settings, database,
        queue_a=queue_a, queue_b=queue_b, repository=repository,
        clock=resolved_clock,
    )
