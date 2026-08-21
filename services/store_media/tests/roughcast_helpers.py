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
    """按页返回预置数据。所有 HTTP 动作都记进 `requests`,便于断言零详情请求。"""

    def __init__(self, pages: list[CrawlPage], *,
                 errors: dict[int, list[CrawlRequestError]] | None = None,
                 page_size: int = 50):
        self._pages = {page.page: page for page in pages}
        self._errors = {key: list(value) for key, value in (errors or {}).items()}
        self._page_size = page_size
        self.requests: list[tuple[str, str]] = []      # (method, target)

    @property
    def page_size(self) -> int:
        return self._page_size

    def search_page(self, page: int) -> CrawlPage:
        self.requests.append(("POST", f"search:page={page}"))
        pending = self._errors.get(page)
        if pending:
            raise pending.pop(0)
        if page not in self._pages:
            raise AssertionError(f"测试未预置第 {page} 页")
        return self._pages[page]


def page_of(rows, page: int, total: int, has_more: bool) -> CrawlPage:
    return CrawlPage(rows=tuple(rows), page=page, total=total, has_more=has_more)


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
