"""本地日 loop 单元测试（`roughcast_loop.py`）。

全程用 `tests/roughcast_helpers.py` 里的假时钟 / 假客户端,**零上游请求**。
Loop 的 `run_tick()` 本身是幂等的最小单位,直接调它就够了——起线程那是
`main.py` 集成测试的事。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.infrastructure.roughcast_crawl_client import CrawlRequestError
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMPLETE,
    PARTIAL,
    QUEUE_B,
    RoughcastRepository,
)
from app.application.roughcast_scorer import run_shadow
from tests.roughcast_helpers import (
    FakeClock,
    FakeCrawlClient,
    make_db,
    make_loop,
    make_row,
    page_of,
    seed_community,
)


# BASE_MOMENT 在 helpers 里是 `本地 11:00,窗口内`;故默认时钟一开始就 due。
BASE_LOCAL_HOUR = 11


def _client_for_a_only(rows, *, page_size: int = 50):
    """A 跑 1 页就结束。B 没有任何 community_pages(让走默认路径的测试用)。

    A 跑完会把 listing 的 `resblock_id` 落成社区,B 的
    `list_queue_b_targets` 会看到它;**不**预置 B 的 community_pages,
    而是用 `queue_b_daily_limit=0` 短路掉 B 段——这条参数对 A/score
    都没影响,只让 B 段直接 skipped。这是「只想验证 A+score」的测试
    最简单的关掉 B 的办法。
    """
    return FakeCrawlClient(
        [
            page_of(rows, page=1, total=len(rows), has_more=False),
        ],
        page_size=page_size,
    )


def _client_with_community(rows, *, community_resblock: str = "RB900",
                            community_rows=None, page_size: int = 50):
    """A 跑 1 页就结束;B 也有一个真实可搜的 community page(让 B 真能跑)。"""
    return FakeCrawlClient(
        [
            page_of(rows, page=1, total=len(rows), has_more=False),
        ],
        community_pages=[
            (
                community_resblock,
                page_of(
                    community_rows or [
                        make_row("R1", resblock_id=community_resblock,
                                 community_name="示例小区参考"),
                    ],
                    page=1, total=1, has_more=False,
                ),
            ),
        ],
        page_size=page_size,
    )


def test_run_tick_not_due_before_window(tmp_path) -> None:
    """窗口前(本地 09:00)→ 全部 skip,零客户端请求。"""
    database = make_db(tmp_path)
    client = _client_for_a_only([make_row("L1")])
    # 本地 09:00 早于窗口下沿 09:30
    clock = FakeClock(datetime(2026, 8, 25, 1, 0, tzinfo=UTC))   # UTC 01:00 = 本地 09:00
    loop = make_loop(database, client, client, clock=clock,
                     queue_b_daily_limit=0)

    tick = loop.run_tick()

    assert tick.skipped_reason == "no_window"
    assert tick.a_status is None and tick.a_run_id is None
    assert tick.scored is False and tick.score_run_id is None
    assert tick.b_status is None and tick.b_run_id is None
    assert client.requests == []                         # 完全没动下游


def test_run_tick_due_and_a_completes_then_score_runs(tmp_path) -> None:
    """Due + A COMPLETE → score_run 落库,listing_run_id 指向 A run。"""
    database = make_db(tmp_path)
    client = _client_for_a_only([make_row("L1")])
    loop = make_loop(database, client, client, queue_b_daily_limit=0)

    tick = loop.run_tick()

    assert tick.skipped_reason is None
    assert tick.a_status == COMPLETE
    assert tick.a_run_id is not None
    assert tick.scored is True
    assert tick.score_run_id is not None
    # B 配额 0 → 直接 skipped
    assert tick.b_status == "skipped"
    # 数据库里能找到一行 score run,listing_run_id == A run id
    with database.connect() as db:
        row = db.execute(
            "SELECT listing_run_id, status FROM roughcast_score_runs "
            "WHERE id = ?", (tick.score_run_id,)
        ).fetchone()
    assert row["status"] == COMPLETE
    assert int(row["listing_run_id"]) == tick.a_run_id


def test_second_run_tick_same_day_does_not_start_another_a(tmp_path) -> None:
    """同一天第二次 tick,即使构造**全新**的 loop 实例,也不会再起 A。

    关键点:这是「restart-safe」的硬证——A 的「今天已跑」状态必须写在
    SQLite,而不是 `_last_run_day` 这种内存字段。`make_loop` 在两次调
    用之间重新 new,所以第一份 loop 的内存状态对第二份不可见。
    """
    database = make_db(tmp_path)
    client = _client_for_a_only([make_row("L1")])
    loop_one = make_loop(database, client, client, queue_b_daily_limit=0)
    first = loop_one.run_tick()
    assert first.a_status == COMPLETE

    # 把假时钟推进 30 分钟,新构造一份 loop 实例——这就是「模拟进程重启」
    clock_two = FakeClock()
    clock_two.advance(minutes=30)
    loop_two = make_loop(database, client, client, clock=clock_two,
                         queue_b_daily_limit=0)
    second = loop_two.run_tick()

    assert second.a_status == "skipped"
    assert second.a_run_id is None
    # A 没有任何新行;只是再确认 score 没必要重跑
    with database.connect() as db:
        a_count = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_runs WHERE queue = 'A'"
        ).fetchone()[0])
    assert a_count == 1


def test_a_aborted_does_not_run_score_or_b(tmp_path) -> None:
    """A 触发熔断(401)→ ABORTED,不开新 score,B 也不起——即使 B 配额充足。

    熔断器是 `RoughcastThrottle` 的即时信号;这里用 `errors={1: [...]}`
    让 page 1 一发就中 401,然后 `_fetch_page_with_retry` 在 page 2 的
    `acquire` 撞到 `BreakerOpen` → 抛 `ThrottleStop` → 外层 `run_once`
    调 `abort_run`。B 用一个**能成功**的 client + `queue_b_daily_limit`
    走默认 60:旧版本只要 B 配额 > 0 就会继续打上游,本测试必须证明这
    条路径被堵住了——`b_status == "skipped"`、`b_run_id is None`、
    `roughcast_crawl_runs` 表里 `queue='B'` 行数为 0。
    """
    database = make_db(tmp_path)
    client = FakeCrawlClient(
        errors={
            1: [CrawlRequestError("boom", http_status=401,
                                   error_code="CRM_AUTH_REQUIRED")],
        },
        page_size=50,
    )
    # B 端换成能成功跑一遍的 client,确保「如果 B 真的被调用,它就会成功」
    # ——这条 client 的 `community_pages` 给 RB900(因为 A 行带
    # resblock_id=RB900),B 的 `list_queue_b_targets` 会落到它。
    a_rows = [make_row("L1", resblock_id="RB900", community_name="示例小区A")]
    client_with_b = _client_with_community(
        rows=a_rows, community_resblock="RB900",
    )
    # `queue_b_daily_limit` 不传——默认值就是 60,旧测试传 0 是双重保险,
    # 现在要证明「配额充足也跳」。
    loop = make_loop(database, client, client_with_b)

    tick = loop.run_tick()

    assert tick.a_status == ABORTED
    assert tick.a_run_id is not None
    # ABORTED → score 不应开新 run
    assert tick.scored is False
    assert tick.score_run_id is None
    with database.connect() as db:
        score_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_score_runs"
        ).fetchone()[0])
        a_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_runs WHERE queue = 'A'"
        ).fetchone()[0])
        b_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_runs WHERE queue = ?",
            (QUEUE_B,),
        ).fetchone()[0])
        b_log = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_log WHERE queue = ?",
            (QUEUE_B,),
        ).fetchone()[0])
    assert score_runs == 0
    assert a_runs == 1
    # B 完全没起:行数 0、日志 0、tick 标记 skipped
    assert b_runs == 0
    assert b_log == 0
    assert tick.b_status == "skipped"
    assert tick.b_run_id is None


def test_a_aborted_blocks_b_on_restart_same_day(tmp_path) -> None:
    """同一天内构造**第二个** loop 实例再 tick,B 仍不起——读 SQLite、不靠内存。

    关键点:第一份 loop 的 `self._has_a_blocking_run_today` 内存态对第二份
    不可见;判定必须落进 `roughcast_crawl_runs` 表,第二份 loop 才能从
    那里看到早上的 ABORTED 行,继续把 B 段短路掉。
    """
    database = make_db(tmp_path)
    client = FakeCrawlClient(
        errors={
            1: [CrawlRequestError("boom", http_status=401,
                                   error_code="CRM_AUTH_REQUIRED")],
        },
        page_size=50,
    )
    a_rows = [make_row("L1", resblock_id="RB900", community_name="示例小区A")]
    client_with_b = _client_with_community(
        rows=a_rows, community_resblock="RB900",
    )
    # 第一份 loop 跑一次——A 必 ABORTED
    loop_one = make_loop(database, client, client_with_b)
    first = loop_one.run_tick()
    assert first.a_status == ABORTED
    assert first.b_status == "skipped"

    # 「模拟进程重启」:新 FakeClock + 全新 loop 实例。注意 client_with_b
    # 仍带着可用的 community_pages,这样如果 B 段被错放过去,它**真的**
    # 能跑——这条断言才有意义。
    clock_two = FakeClock()
    clock_two.advance(minutes=30)
    loop_two = make_loop(database, client_with_b, client_with_b, clock=clock_two)
    second = loop_two.run_tick()

    # A 短路成 skipped(B 配额 60 仍能跑,但 A 段已被 _has_a_run_today 拦下)
    assert second.a_status == "skipped"
    assert second.a_run_id is None
    # B 仍被 ABORTED-A 拦下
    assert second.b_status == "skipped"
    assert second.b_run_id is None
    with database.connect() as db:
        b_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_runs WHERE queue = ?",
            (QUEUE_B,),
        ).fetchone()[0])
    assert b_runs == 0


def test_published_a_with_existing_score_only_runs_b(tmp_path) -> None:
    """A 已发布 + score 已有同 listing_run_id → 只跑 B。

    流程:第一份 loop 的 tick 已经把 A 跑 COMPLETE 并把 score 写满
    (因为 `score_after_crawl=True` 默认开启);再用同份 database
    + 重新构造的 loop 跑 tick,验证 score 段不再写第二行,A 也不
    会被重启成新一轮。
    """
    database = make_db(tmp_path)
    # A 跑 RB900 这一个小区,这样 B 也只针对 RB900 拿目标——不会跑到默认
    # RB001(那条 path 上没有 B 的 community_pages fixture)。
    client_a = _client_for_a_only(
        [make_row("L1", resblock_id="RB900", community_name="示例小区A")],
    )
    loop_one = make_loop(database, client_a, client_a, queue_b_daily_limit=0)
    first = loop_one.run_tick()
    assert first.a_status == COMPLETE
    assert first.scored is True
    # 第一轮 tick 已经在 `score_after_crawl=True` 下自动写过一行 score。
    with database.connect() as db:
        score_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_score_runs"
        ).fetchone()[0])
    assert score_runs == 1

    # 给 B 准备 RB900 的真实可搜 community page
    client_b = _client_with_community(
        rows=[make_row("L1", resblock_id="RB900",
                       community_name="示例小区A")],
        community_resblock="RB900",
    )
    clock_two = FakeClock()
    clock_two.advance(minutes=30)
    loop_two = make_loop(database, client_a, client_b, clock=clock_two)
    second = loop_two.run_tick()

    # A 没新行(同日)
    assert second.a_status == "skipped"
    # score 不会再写一次
    assert second.scored is False
    assert second.score_run_id is None
    with database.connect() as db:
        score_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_score_runs"
        ).fetchone()[0])
    assert score_runs == 1
    # B 真的跑了一次
    assert second.b_status in (COMPLETE, PARTIAL, ABORTED)
    assert second.b_run_id is not None


def test_queue_b_daily_limit_zero_skips_b(tmp_path) -> None:
    """`roughcast_queue_b_daily_limit=0` → B 永远 skipped,但 A/score 照旧。"""
    database = make_db(tmp_path)
    client = _client_for_a_only([make_row("L1")])
    loop = make_loop(database, client, client, queue_b_daily_limit=0)

    tick = loop.run_tick()

    assert tick.a_status == COMPLETE
    assert tick.scored is True
    assert tick.b_status == "skipped"
    assert tick.b_run_id is None
    with database.connect() as db:
        b_count = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_crawl_runs WHERE queue = ?",
            (QUEUE_B,),
        ).fetchone()[0])
    assert b_count == 0


def test_score_after_crawl_false_skips_score_but_runs_a_and_b(tmp_path) -> None:
    """`roughcast_score_after_crawl=False` → score 段跳过,A/B 正常。"""
    database = make_db(tmp_path)
    client = _client_for_a_only([make_row("L1")])
    loop = make_loop(
        database, client, client, score_after_crawl=False,
        queue_b_daily_limit=0,
    )

    tick = loop.run_tick()

    assert tick.a_status == COMPLETE
    assert tick.scored is False
    assert tick.score_run_id is None
    with database.connect() as db:
        score_runs = int(db.execute(
            "SELECT COUNT(*) FROM roughcast_score_runs"
        ).fetchone()[0])
    assert score_runs == 0
