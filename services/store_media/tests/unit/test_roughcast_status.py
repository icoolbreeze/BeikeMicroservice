"""观察期只读盘点视图（§六 第 1 期出口条件 / §七.7）。

这里的每个断言都对应一条出口条件或一条纪律。视图本身只 SELECT，所以测试也
不需要任何 fake 客户端——直接用 repository 造出「跑了 3 天」的库。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.application.roughcast_crawler import RoughcastCrawlConfig
from app.application.roughcast_status import (
    REQUIRED_DAYS,
    _display_width,
    _table,
    build_status_report,
    render_status,
)
from app.infrastructure.roughcast_repository import COMPLETE, RoughcastRepository
from tests.roughcast_helpers import FakeClock, make_db, make_row

QUEUE = "A"
CONFIG = RoughcastCrawlConfig()
# 本地 11:00（UTC+8）,采集窗口 09:30–19:00 之内。
DAY_ONE = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def make_repository(tmp_path, clock: FakeClock) -> RoughcastRepository:
    return RoughcastRepository(make_db(tmp_path), clock=clock)


def run_a_day(repository: RoughcastRepository, clock: FakeClock, rows, *,
              pages: int = 2, total: int = 3289, requests: int = 3,
              complete: bool = True, gap_seconds: float = 30.0) -> int:
    """造一轮:start → 按节奏记 N 次请求 → stage → 记页数与 total → 收尾。"""
    run_id = repository.start_run(QUEUE)
    for page in range(1, requests + 1):
        log_id = repository.log_request(
            run_id=run_id, queue=QUEUE, target=f"page={page}", status="issued"
        )
        repository.update_request_log(log_id, status="ok", http_status=200)
        clock.advance(seconds=gap_seconds)
    repository.stage_rows(run_id, rows)
    repository.record_progress(
        run_id, pages_done=pages, pages_expected=pages, upstream_total=total
    )
    repository.sync_run_request_count(run_id)
    if complete:
        repository.complete_run(run_id)
    else:
        repository.abort_run(run_id, "breaker_open")
    return run_id


def build(repository: RoughcastRepository, clock: FakeClock, *, limit: int = 10):
    return build_status_report(repository, CONFIG, limit=limit, clock=clock)


# --------------------------------------------------------------- run 摘要


def test_run_summary_carries_the_numbers_worth_watching(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_id = run_a_day(
        repository, clock,
        [make_row("L1"), make_row("L2"), make_row("L3", fitment_status="003")],
    )

    report = build(repository, clock)

    assert len(report.runs) == 1
    line = report.runs[0]
    assert (line.run_id, line.status) == (run_id, COMPLETE)
    assert line.published is True
    assert line.upstream_total == 3289
    assert line.request_count == 3
    # 毛坯只数 fitment=002,非毛坯行照 §七.8 落库并单独计数。
    assert (line.roughcast_staged, line.non_roughcast) == (2, 1)
    assert line.snapshots_inserted == 2
    assert line.started_local.hour == 11         # UTC+8 的本地时间,不是 UTC


def test_second_day_writes_far_fewer_snapshots_than_it_publishes(tmp_path) -> None:
    """4.5 那 36 倍收益的验收信号:第 2 天起新快照应远低于毛坯数。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    rows = [make_row(f"L{index}") for index in range(10)]
    run_a_day(repository, clock, rows)
    clock.advance(days=1)
    # 只有一套改了价,其余 9 套内容未变。
    changed = [*rows[:9], make_row("L9", monthly_rent_yuan=4500.0)]
    run_a_day(repository, clock, changed)

    latest = build(repository, clock).runs[-1]
    assert latest.roughcast_staged == 10
    assert latest.snapshots_inserted == 1

    rendered = render_status(build(repository, clock), CONFIG)
    assert "变更点率:1/10 = 10.0%" in rendered
    assert "正常" in rendered


def test_non_complete_run_writing_snapshots_is_flagged(tmp_path) -> None:
    """4.2 说这类越界发布「最难通过抽查发现」,所以视图直接把它喊出来。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], complete=False)

    report = build(repository, clock)
    assert report.runs[0].snapshots_inserted == 0
    assert report.runs[0].leaked_snapshots is False

    # 手工伪造一次越界发布,确认视图会发现。
    with repository.database.connect() as db:
        db.execute(
            "UPDATE roughcast_listing_snapshot SET captured_run_id = ?",
            (report.runs[0].run_id,),
        )
        db.execute(
            "INSERT INTO roughcast_listing_snapshot "
            "(listing_id, captured_at, captured_run_id, last_confirmed_at, "
            " last_confirmed_run_id, content_hash, community_name) "
            "VALUES ('L1', ?, ?, ?, ?, 'deadbeef', '示例小区')",
            (clock.now.isoformat(), report.runs[0].run_id,
             clock.now.isoformat(), report.runs[0].run_id),
        )

    leaked = build(repository, clock).runs[0]
    assert leaked.leaked_snapshots is True
    assert "违反 4.2" in render_status(build(repository, clock), CONFIG)


# ------------------------------------------------------------ total 波动


def test_total_drift_tracks_span_across_days(tmp_path) -> None:
    """§七.7:连续 3 天记录 total,观察波动幅度。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    for total in (3289, 3291, 3290):
        run_a_day(repository, clock, [make_row("L1")], total=total)
        clock.advance(days=1)

    drift = build(repository, clock).drift

    assert drift.values == (3289, 3291, 3290)
    assert (drift.low, drift.high, drift.span) == (3289, 3291, 2)
    assert drift.span_pct < 0.1
    assert len(drift.days) == 3


def test_aborted_run_still_counts_as_a_total_sample(tmp_path) -> None:
    """ABORTED 轮的第 1 页 total 仍是一次有效测量——它只是没资格发布。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], total=3300, complete=False)

    report = build(repository, clock)
    assert report.drift.values == (3300,)
    assert report.exit_progress.total_days == (date(2026, 8, 18),)
    assert report.exit_progress.complete_days == ()      # 但不算「产出 COMPLETE run」


# ---------------------------------------------------------------- 日流量


def test_traffic_flags_intervals_below_the_floor(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], requests=3, gap_seconds=5.0)

    day = build(repository, clock).days[-1]

    assert day.requests == 3
    assert day.min_gap == 5.0
    assert day.interval_violations == 2                  # 3 次请求 → 2 个间隔
    assert build(repository, clock).exit_progress.traffic_ok is False


def test_traffic_accepts_compliant_pacing(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], requests=4, gap_seconds=40.0)

    day = build(repository, clock).days[-1]

    assert (day.interval_violations, day.outside_window, day.breakers) == (0, 0, 0)
    assert day.median_gap == 40.0
    assert day.complete_runs == 1


def test_requests_outside_the_window_are_counted(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 18, 14, 0, tzinfo=UTC))     # 本地 22:00
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], requests=2)

    assert build(repository, clock).days[-1].outside_window == 2
    assert build(repository, clock).exit_progress.traffic_ok is False


def test_breaker_rows_are_listed_and_excluded_from_the_pace(tmp_path) -> None:
    """breaker 行是 trip() 的告警留痕,不是一次上游请求——不能让它伪造 0 秒间隔。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    log_id = repository.log_request(
        run_id=None, queue=QUEUE, target="page=1", status="issued"
    )
    repository.update_request_log(log_id, status="ok", http_status=200)
    repository.log_request(
        run_id=None, queue=QUEUE, target="breaker", status="breaker", note="http_429"
    )

    report = build(repository, clock)

    assert report.days[-1].requests == 1
    assert report.days[-1].gaps == ()
    assert report.days[-1].breakers == 1
    assert [event.note for event in report.breakers] == ["http_429"]
    assert report.exit_progress.traffic_ok is False
    assert "http_429" in render_status(report, CONFIG)


# ------------------------------------------------------------- 出口条件


def test_three_consecutive_days_satisfies_the_phase_one_exit(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    for _ in range(REQUIRED_DAYS):
        run_a_day(repository, clock, [make_row("L1")], requests=3, gap_seconds=40.0)
        clock.advance(days=1)

    progress = build(repository, clock).exit_progress

    assert progress.consecutive_complete_days == 3
    assert len(progress.total_days) == 3
    assert progress.traffic_ok is True
    assert progress.ready is True
    assert "可以开第 3 期" in render_status(build(repository, clock), CONFIG)


def test_a_skipped_day_restarts_the_streak(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")], gap_seconds=40.0)
    clock.advance(days=2)                                # 中间断了一天
    run_a_day(repository, clock, [make_row("L1")], gap_seconds=40.0)

    progress = build(repository, clock).exit_progress

    assert progress.complete_days == (date(2026, 8, 18), date(2026, 8, 20))
    assert progress.consecutive_complete_days == 1
    assert progress.ready is False


def test_exit_progress_ignores_the_display_limit(tmp_path) -> None:
    """出口条件问的是「连续 3 天」,答案不该因为 `--status 1` 就变成 1 天。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    for _ in range(REQUIRED_DAYS):
        run_a_day(repository, clock, [make_row("L1")], requests=3, gap_seconds=40.0)
        clock.advance(days=1)

    report = build(repository, clock, limit=1)

    assert len(report.runs) == 1                          # 表格只显示 1 轮
    assert report.exit_progress.consecutive_complete_days == 3
    assert report.exit_progress.ready is True


def test_traffic_with_no_requests_is_not_called_compliant(tmp_path) -> None:
    """没观察到不等于合规——空库不该给出口条件盖个「符合」的章。"""
    clock = FakeClock(DAY_ONE)
    report = build(make_repository(tmp_path, clock), clock)

    assert report.exit_progress.observed_requests == 0
    assert report.exit_progress.traffic_ok is False
    assert "尚无请求可看" in render_status(report, CONFIG)


# ---------------------------------------------------------------- 渲染


def test_empty_database_renders_a_hint_instead_of_crashing(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)

    rendered = render_status(build(repository, clock), CONFIG,
                             database_path=tmp_path / "store_media.sqlite3")

    assert "还没有任何 run" in rendered
    assert "roughcast_crawl_once.py" in rendered
    assert "连续 COMPLETE 天数 0/3" in rendered


def test_render_aligns_wide_characters() -> None:
    """中文双宽,`str.ljust` 会把表头和数据列错开一大截——所以按显示宽度补齐。"""
    rendered = _table(
        ["日期", "请求", "熔断"],
        [["08-18", "67", "0"], ["08-19", "3291", "12"]],
        aligns="lrr",
    )

    widths = {_display_width(line) for line in rendered}
    assert len(widths) == 1                       # 每行等宽 → 列必然对齐
    assert rendered[1].endswith("67     0")       # 数字右对齐到表头右缘


def test_status_view_writes_nothing(tmp_path) -> None:
    """观察期视图必须只读:它会被随手跑很多遍,不能动 crawl_log 的账。"""
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")])

    before = _fingerprint(repository)
    for _ in range(3):
        render_status(build(repository, clock), CONFIG)
    assert _fingerprint(repository) == before


def _fingerprint(repository: RoughcastRepository) -> tuple:
    with repository.database.connect() as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("roughcast_crawl_runs", "roughcast_crawl_log",
                          "roughcast_crawl_stage", "roughcast_listing_current",
                          "roughcast_listing_snapshot", "roughcast_communities")
        )


def test_limit_keeps_only_the_most_recent_runs(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    for index in range(4):
        run_a_day(repository, clock, [make_row("L1")], total=3280 + index)
        clock.advance(hours=1)

    report = build(repository, clock, limit=2)

    assert [line.upstream_total for line in report.runs] == [3282, 3283]


def test_traffic_window_only_looks_back_a_week(tmp_path) -> None:
    clock = FakeClock(DAY_ONE)
    repository = make_repository(tmp_path, clock)
    run_a_day(repository, clock, [make_row("L1")])
    clock.now = DAY_ONE + timedelta(days=30)
    run_a_day(repository, clock, [make_row("L1")])

    report = build(repository, clock)

    assert [day.day for day in report.days] == [date(2026, 9, 17)]
    assert len(report.runs) == 2                 # run 摘要不受 7 天窗口约束
