"""队列 A 的翻页、终止条件与终态映射（§三 / §六 / §七.7）。"""

from __future__ import annotations

from datetime import UTC, datetime, time as dtime

from app.application.roughcast_crawler import (
    PARTIAL,
    RoughcastCrawlConfig,
    crawl_config_from_settings,
)
from app.infrastructure.roughcast_crawl_client import CrawlRequestError, format_price_range
from app.infrastructure.roughcast_repository import ABORTED, COMPLETE, FAILED, RoughcastRepository
from app.infrastructure.settings import Settings
from tests.roughcast_helpers import (
    FakeClock,
    FakeCrawlClient,
    make_crawler,
    make_db,
    make_row,
    page_of,
)


def config(**overrides) -> RoughcastCrawlConfig:
    """把停顿压到 0,让测试只验证控制流,不验证等了多久（那是节流器的测试）。"""
    base = {
        "page_size": 2,
        "page_gap_seconds": (0.0, 0.0),
        "long_pause_seconds": (0.0, 0.0),
        "min_request_interval_seconds": 0.0,
    }
    return RoughcastCrawlConfig(**{**base, **overrides})


def three_pages() -> FakeCrawlClient:
    return FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=5, has_more=True),
            page_of([make_row("L3"), make_row("L4")], page=2, total=5, has_more=True),
            page_of([make_row("L5")], page=3, total=5, has_more=False),
        ],
        page_size=2,
    )


# ------------------------------------------------------------------ 翻页


def test_pages_expected_is_derived_from_total(tmp_path) -> None:
    """§七.7:页数由第 1 页的 total 现算,66 不得写成常数。"""
    database = make_db(tmp_path)
    crawler = make_crawler(database, three_pages(), config=config())

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE
    assert outcome.pages_expected == 3          # ceil(5 / 2)
    assert outcome.pages_done == 3
    assert outcome.published == 5
    run = RoughcastRepository(database).get_run(outcome.run_id)
    assert run["upstream_total"] == 5


def test_pagination_covers_every_page_exactly_once(tmp_path) -> None:
    client = three_pages()
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    crawler.run_once()

    # V2.5:默认 `bucket_plan=((None, None),)` 渲染为 `0:-1`(`format_price_range`),
    # 所有 search 都带 `bucket=` 前缀。11 档切分的测试用 `bucket_plan=...` 覆盖。
    assert client.requests == [
        ("POST", "search:bucket=0:-1&page=1"),
        ("POST", "search:bucket=0:-1&page=2"),
        ("POST", "search:bucket=0:-1&page=3"),
    ]


def test_no_detail_request_is_issued_during_crawl(tmp_path) -> None:
    """§三:抓取阶段禁止打详情接口。客户端连详情方法都没有,这里守住回归。"""
    client = three_pages()
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    crawler.run_once()

    assert all(method == "POST" and target.startswith("search:")
               for method, target in client.requests)
    assert not hasattr(client, "detail")
    assert not any(hasattr(type(crawler.client), name)
                   for name in ("detail", "listing_detail", "prospect", "get_detail"))


def test_has_more_false_terminates_even_when_total_is_zero(tmp_path) -> None:
    """total 不可信(为 0)时退回 has_more,不能因为算不出页数就空跑一轮。"""
    client = FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=0, has_more=True),
            page_of([make_row("L3")], page=2, total=0, has_more=False),
        ],
        page_size=2,
    )
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE
    assert outcome.pages_done == 2
    assert outcome.pages_expected == 2
    assert outcome.published == 3


def test_has_more_false_early_reconciles_expected_pages(tmp_path) -> None:
    """total 跨页波动是常态。为「total 掉了一行」判 ABORTED 是过度反应。"""
    client = FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=6, has_more=True),
            page_of([make_row("L3")], page=2, total=6, has_more=False),
        ],
        page_size=2,
    )
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE            # 而不是判页数不一致
    assert outcome.pages_done == 2
    assert outcome.pages_expected == 2           # 对齐到实际翻完的页数
    assert client.requests == [("POST", "search:bucket=0:-1&page=1"), ("POST", "search:bucket=0:-1&page=2")]


def test_growing_total_does_not_chase_the_tail(tmp_path) -> None:
    """total 在采集期间涨了:不追这条会一直增长的尾巴,明天那一轮会覆盖。"""
    client = FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=4, has_more=True),
            page_of([make_row("L3"), make_row("L4")], page=2, total=99, has_more=True),
        ],
        page_size=2,
    )
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE
    assert outcome.pages_done == 2               # 停在按第 1 页 total 算出的 2 页
    assert len(client.requests) == 2


# ------------------------------------------------------------------ 预算 / 重试


def test_retry_consumes_budget_and_still_completes(tmp_path) -> None:
    """§八 G:重试也走 acquire,所以 2 页 + 1 次重试 = 3 次请求。"""
    client = FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=3, has_more=True),
            page_of([make_row("L3")], page=2, total=3, has_more=False),
        ],
        errors={2: [CrawlRequestError("上游 500", http_status=500)]},
        page_size=2,
    )
    crawler = make_crawler(make_db(tmp_path), client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE
    assert outcome.requests == 3
    assert len(client.requests) == 3


def test_exhausted_retries_fail_the_run(tmp_path) -> None:
    client = FakeCrawlClient(
        [page_of([make_row("L1")], page=1, total=1, has_more=False)],
        errors={1: [CrawlRequestError("上游 500", http_status=500)] * 2},
        page_size=2,
    )
    crawler = make_crawler(make_db(tmp_path), client, config=config(retry_reserve=1))

    outcome = crawler.run_once()

    assert outcome.status == FAILED
    assert outcome.published == 0


def test_breaker_signal_aborts_the_run(tmp_path) -> None:
    """429 是即时熔断信号:当轮判 ABORTED,且不再发请求。"""
    client = FakeCrawlClient(
        [
            page_of([make_row("L1"), make_row("L2")], page=1, total=6, has_more=True),
            page_of([make_row("L3")], page=2, total=6, has_more=False),
        ],
        errors={2: [CrawlRequestError("上游限流", http_status=429)]},
        page_size=2,
    )
    database = make_db(tmp_path)
    crawler = make_crawler(database, client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "breaker_open"
    assert len(client.requests) == 2             # 熔断后一次都不再发
    # 中止的一轮不发布。
    assert RoughcastRepository(database).active_count() == 0


def test_budget_exhaustion_aborts_the_run(tmp_path) -> None:
    client = three_pages()
    crawler = make_crawler(make_db(tmp_path), client, config=config(daily_request_cap=2))

    outcome = crawler.run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "budget_exhausted"
    assert len(client.requests) == 2
    assert outcome.pages_done == 2


def test_budget_is_tightened_after_the_first_page(tmp_path) -> None:
    """硬顶起手,拿到 total 后立刻收紧到推导值——中间不存在超支窗口。"""
    database = make_db(tmp_path)
    client = three_pages()
    crawler = make_crawler(
        database, client, config=config(retry_reserve=1, safety_factor=1.0, daily_request_cap=260)
    )
    captured: list[int] = []
    original = crawler._tighten_budget                    # noqa: SLF001

    def spy(throttle, pages_expected):
        original(throttle, pages_expected)
        captured.append(throttle.daily_budget)

    crawler._tighten_budget = spy                         # noqa: SLF001
    crawler.run_once()

    assert captured == [4]                                # 3 页 + 1 次重试预留


# ------------------------------------------------------------------ 窗口


def test_closed_window_aborts_before_any_request(tmp_path) -> None:
    client = three_pages()
    # 本地 20:00,窗口 09:30–19:00 之外。
    clock = FakeClock(datetime(2026, 8, 20, 12, 0, tzinfo=UTC))
    database = make_db(tmp_path)
    crawler = make_crawler(database, client, clock=clock, config=config())

    outcome = crawler.run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "window_closed"
    assert client.requests == []
    run = RoughcastRepository(database, clock=clock).get_run(outcome.run_id)
    assert run["abort_reason"] == "window_closed"


def test_window_closing_mid_crawl_aborts_without_publishing(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 20, 10, 55, tzinfo=UTC))    # 本地 18:55
    client = three_pages()
    database = make_db(tmp_path)
    crawler = make_crawler(database, client, clock=clock, config=config())
    real_search = client.search_page

    def search_then_age(page: int, **kwargs):
        result = real_search(page, **kwargs)
        clock.advance(minutes=10)      # 第 1 页之后就出窗口了
        return result

    client.search_page = search_then_age
    outcome = crawler.run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "window_closed"
    assert outcome.pages_done == 1
    assert RoughcastRepository(database, clock=clock).active_count() == 0


# ------------------------------------------------------------------ 收尾


def test_stale_running_run_is_reaped_on_next_pass(tmp_path) -> None:
    clock = FakeClock()
    database = make_db(tmp_path)
    abandoned = RoughcastRepository(database, clock=clock).start_run("A")

    clock.advance(hours=13)
    crawler = make_crawler(database, three_pages(), clock=clock, config=config())
    crawler.run_once()

    run = RoughcastRepository(database, clock=clock).get_run(abandoned)
    assert run["status"] == FAILED
    assert run["abort_reason"] == "stale_running_run_reaped"


def test_unexpected_exception_leaves_a_terminal_run(tmp_path) -> None:
    """任何异常都必须留下终态,不能把 run 悬挂在 RUNNING。"""
    client = three_pages()

    def explode(page: int, **kwargs):
        raise ZeroDivisionError("装配错误,不是上游问题")

    client.search_page = explode
    database = make_db(tmp_path)
    crawler = make_crawler(database, client, config=config())

    outcome = crawler.run_once()

    assert outcome.status == FAILED
    assert "ZeroDivisionError" in outcome.reason
    run = RoughcastRepository(database).get_run(outcome.run_id)
    assert run["status"] == FAILED
    # 终态原因要带上异常类型,否则「装配错误」和「上游错误」在日志里长得一样。
    assert run["abort_reason"] == outcome.reason


# ------------------------------------------------------------------ 11 档编排（V2.5）


def test_eleven_buckets_all_succeed_returns_complete(tmp_path) -> None:
    """V2.5 11 档切分:全 success → COMPLETE,每档的 request 形如
    `bucket=lo:hi&page=N` 且每档的 stage / publish 都独立。

    3 档的简版足够覆盖逻辑——11 档只是循环更多次。深分页硬顶见
    [[roughcast-deep-paging-cap]]。
    """
    three_bucket_plan = ((0, 800), (800, 1200), (1200, 1500))
    client = FakeCrawlClient([
        page_of([make_row("A1"), make_row("A2")], page=1, total=2, has_more=False,
                price_range="0:800"),
        page_of([make_row("B1"), make_row("B2")], page=1, total=2, has_more=False,
                price_range="800:1200"),
        page_of([make_row("C1"), make_row("C2")], page=1, total=2, has_more=False,
                price_range="1200:1500"),
    ], page_size=2)
    crawler = make_crawler(make_db(tmp_path), client,
                           config=config(bucket_plan=three_bucket_plan))

    outcome = crawler.run_once()

    assert outcome.status == COMPLETE
    assert outcome.published == 6
    assert set(outcome.bucket_outcomes) == {"0:800", "800:1200", "1200:1500"}
    assert all(v == "success" for v in outcome.bucket_outcomes.values())
    # 3 档各发了 1 次 page=1,共 3 次 search;无 detail 请求。
    assert client.requests == [
        ("POST", "search:bucket=0:800&page=1"),
        ("POST", "search:bucket=800:1200&page=1"),
        ("POST", "search:bucket=1200:1500&page=1"),
    ]


def test_one_bucket_over_cap_keeps_status_partial(tmp_path) -> None:
    """V2.5:一档 totalCount ≥ 1000 → PARTIAL,该档不发布,其他档照常发。"""
    three_bucket_plan = ((0, 800), (800, 1200), (1200, 1500))
    client = FakeCrawlClient([
        page_of([make_row("A1"), make_row("A2")], page=1, total=2, has_more=False,
                price_range="0:800"),
        # 中间这档 total=1500,触发 over_cap
        page_of([make_row("BAD1"), make_row("BAD2")], page=1, total=1500,
                has_more=False, price_range="800:1200"),
        page_of([make_row("C1"), make_row("C2")], page=1, total=2, has_more=False,
                price_range="1200:1500"),
    ], page_size=2)
    database = make_db(tmp_path)
    crawler = make_crawler(database, client,
                           config=config(bucket_plan=three_bucket_plan))

    outcome = crawler.run_once()

    assert outcome.status == PARTIAL
    # 成功档 4 套 + 失败档 2 套被 stage 但 publish 时被过滤。
    assert outcome.bucket_outcomes == {
        "0:800": "success",
        "800:1200": "skipped_over_cap",
        "1200:1500": "success",
    }
    # over_cap 档的 stage 行被 complete_run 过滤,只发成功档。
    assert outcome.published == 4
    # 数据库里只看到 4 套活跃——失败档不进入 listing_current。
    active = RoughcastRepository(database).active_count()
    assert active == 4
    # 过 cap 那档的房源在数据库里没出现。
    assert RoughcastRepository(database).get_current("BAD1") is None


def test_one_bucket_failure_does_not_kill_others(tmp_path) -> None:
    """V2.5:一档 page 1 之后的所有页都失败 → 该档 bucket_failed,其他档照发。"""
    three_bucket_plan = ((0, 800), (800, 1200), (1200, 1500))
    client = FakeCrawlClient([
        page_of([make_row("A1"), make_row("A2")], page=1, total=2, has_more=False,
                price_range="0:800"),
        # 中间这档 2 页,page 2 抛 500
        page_of([make_row("B1")], page=1, total=3, has_more=True,
                price_range="800:1200"),
        page_of([make_row("C1"), make_row("C2")], page=1, total=2, has_more=False,
                price_range="1200:1500"),
    ], errors={2: [CrawlRequestError("上游 500", http_status=500)] * 2},
       page_size=2)
    database = make_db(tmp_path)
    crawler = make_crawler(database, client,
                           config=config(bucket_plan=three_bucket_plan,
                                        retry_reserve=1))

    outcome = crawler.run_once()

    assert outcome.status == PARTIAL
    assert outcome.bucket_outcomes["0:800"] == "success"
    assert outcome.bucket_outcomes["800:1200"] == "bucket_failed"
    assert outcome.bucket_outcomes["1200:1500"] == "success"
    # B1 在失败档里,被过滤;其余 4 套成功。
    assert outcome.published == 4
    assert RoughcastRepository(database).active_count() == 4
    assert RoughcastRepository(database).get_current("B1") is None


# ------------------------------------------------------------------ 装配


def test_config_is_built_from_settings(tmp_path) -> None:
    settings = Settings(
        storage_dir=tmp_path,
        roughcast_crawl_window=("10:15", "18:45"),
        roughcast_daily_request_cap=120,
        roughcast_crawl_page_size=25,
    )

    built = crawl_config_from_settings(settings)

    assert built.window_start == dtime(10, 15)
    assert built.window_end == dtime(18, 45)
    assert built.daily_request_cap == 120
    assert built.page_size == 25
    assert built.timezone.total_seconds() == 8 * 3600
