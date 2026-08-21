"""run 状态机、变更点写入与发布事务（§4.2 / §4.5 / §八 Q·R·U）。"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.roughcast import BUSINESS_FIELDS, content_hash
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMPLETE,
    FAILED,
    RUNNING,
    RoughcastRepository,
    RunStateError,
)
from tests.roughcast_helpers import FakeClock, make_db, make_row

QUEUE = "A"


def publish(repository: RoughcastRepository, rows, *, pages: int = 1) -> dict[str, int]:
    """跑一整轮:start → stage → 补齐页数 → COMPLETE。"""
    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, rows)
    repository.record_progress(run_id, pages_done=pages, pages_expected=pages)
    return {"run_id": run_id, **repository.complete_run(run_id)}


# ------------------------------------------------------------------ 表结构


@pytest.mark.parametrize("table", ["roughcast_listing_current", "roughcast_listing_snapshot"])
def test_business_columns_match_the_domain_row(tmp_path, table) -> None:
    """4.1 要求 current 与 snapshot 的业务列同构,且与入库模型一一对应。

    比对真实的 `PRAGMA table_info`,而不是两份各自派生的常量——后者会一起错。
    """
    database = make_db(tmp_path)
    with database.connect() as db:
        columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]

    business = [name for name in columns if name in set(BUSINESS_FIELDS)]
    assert tuple(business) == BUSINESS_FIELDS


# ------------------------------------------------------------------ 状态机


def test_run_reaches_complete_and_publishes(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    result = publish(repository, [make_row("L1"), make_row("L2")])

    assert result["published"] == 2
    assert result["snapshots_inserted"] == 2
    assert repository.get_run(result["run_id"])["status"] == COMPLETE
    assert repository.active_count() == 2


def test_complete_requires_all_pages_done(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, [make_row("L1")])
    repository.record_progress(run_id, pages_done=1, pages_expected=3)

    with pytest.raises(RunStateError):
        repository.complete_run(run_id)
    assert repository.get_run(run_id)["status"] == RUNNING
    assert repository.active_count() == 0


def test_aborted_run_publishes_nothing(tmp_path) -> None:
    """V1 的数据损坏 bug:中止的一轮把 listing_current 刷成了半截状态。"""
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    publish(repository, [make_row("L1"), make_row("L2")])
    before = {row: dict(repository.get_current(row)) for row in ("L1", "L2")}

    doomed = repository.start_run(QUEUE)
    repository.stage_rows(doomed, [make_row("L1", monthly_rent_yuan=9999.0)])
    repository.abort_run(doomed, "breaker_open")

    run = repository.get_run(doomed)
    assert run["status"] == ABORTED
    assert run["abort_reason"] == "breaker_open"
    # current 一个字节都不能动,尤其不能把没在这一轮出现的 L2 判下架。
    assert {row: dict(repository.get_current(row)) for row in ("L1", "L2")} == before
    assert repository.active_count() == 2
    assert len(repository.snapshots_for("L1")) == 1
    # 数据止步于 stage,不是被丢弃。
    assert repository.stage_count(doomed) == 1


def test_failed_run_publishes_nothing(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, [make_row("L1")])
    repository.fail_run(run_id, "ValueError: boom")

    assert repository.get_run(run_id)["status"] == FAILED
    assert repository.active_count() == 0
    assert repository.snapshots_for("L1") == []


def test_terminal_run_cannot_be_completed(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    repository.record_progress(run_id, pages_done=1, pages_expected=1)
    repository.abort_run(run_id, "window_closed")

    with pytest.raises(RunStateError):
        repository.complete_run(run_id)


def test_stale_running_run_is_reaped(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    abandoned = repository.start_run(QUEUE)      # 进程被 kill,没机会写终态

    clock.advance(hours=13)
    reaped = repository.reap_stale_runs(max_age=timedelta(hours=12))

    assert reaped == [abandoned]
    run = repository.get_run(abandoned)
    assert run["status"] == FAILED
    assert run["abort_reason"] == "stale_running_run_reaped"


def test_fresh_running_run_is_not_reaped(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    live = repository.start_run(QUEUE)

    clock.advance(hours=1)
    assert repository.reap_stale_runs(max_age=timedelta(hours=12)) == []
    assert repository.get_run(live)["status"] == RUNNING


def test_latest_complete_run_ignores_aborted(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    good = publish(repository, [make_row("L1")])["run_id"]

    clock.advance(days=1)
    doomed = repository.start_run(QUEUE)
    repository.abort_run(doomed, "budget_exhausted")

    assert repository.latest_complete_run(QUEUE)["id"] == good


def test_complete_is_atomic(tmp_path) -> None:
    """发布中途抛异常必须整体回滚:没有半更新,run 也不会停在 COMPLETE。"""
    database = make_db(tmp_path)
    repository = RoughcastRepository(database, clock=FakeClock())
    publish(repository, [make_row("L1"), make_row("L2")])
    before = {row: dict(repository.get_current(row)) for row in ("L1", "L2")}
    snapshots_before = len(repository.snapshots_for("L1"))

    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, [make_row("L1", monthly_rent_yuan=5000.0)])
    repository.record_progress(run_id, pages_done=1, pages_expected=1)

    original = repository._refresh_current                       # noqa: SLF001
    def explode(*args, **kwargs):
        original(*args, **kwargs)
        raise sqlite3.OperationalError("模拟刷 current 中途失败")
    repository._refresh_current = explode                        # noqa: SLF001

    with pytest.raises(sqlite3.OperationalError):
        repository.complete_run(run_id)

    assert repository.get_run(run_id)["status"] == RUNNING
    assert {row: dict(repository.get_current(row)) for row in ("L1", "L2")} == before
    assert len(repository.snapshots_for("L1")) == snapshots_before


# ------------------------------------------------------------------ 变更点


def test_change_point_writes_one_row_per_change(tmp_path) -> None:
    """§八 Q:5 轮采集、第 3 轮改价,快照表必须正好 2 行,区间首尾对得上。"""
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    moments = []

    for index in range(1, 6):
        rent = 4300.0 if index < 3 else 4500.0
        moments.append(clock.now.isoformat())
        publish(repository, [make_row("L1", monthly_rent_yuan=rent)])
        clock.advance(days=1)

    snapshots = repository.snapshots_for("L1")
    assert len(snapshots) == 2
    assert [row["monthly_rent_yuan"] for row in snapshots] == [4300.0, 4500.0]
    # 第 1 段:第 1 轮开始,第 2 轮最后一次确认。
    assert snapshots[0]["captured_at"] == moments[0]
    assert snapshots[0]["last_confirmed_at"] == moments[1]
    # 第 2 段:第 3 轮开始,第 5 轮最后一次确认。
    assert snapshots[1]["captured_at"] == moments[2]
    assert snapshots[1]["last_confirmed_at"] == moments[4]


def test_no_change_over_sixty_runs_keeps_one_fresh_snapshot(tmp_path) -> None:
    """§八 R:60 轮无变化只有 1 行,而新鲜度读 last_confirmed_at(不是 captured_at)。

    如果新鲜度错读 captured_at,这里的 reference_age 会是 59 天、w_fresh 直接归零,
    而第 4 期的评分会安静地把所有房源都判成「参考数据过期」。
    """
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)

    for _ in range(60):
        publish(repository, [make_row("L1")])
        clock.advance(days=1)

    snapshots = repository.snapshots_for("L1")
    assert len(snapshots) == 1
    last_confirmed = snapshots[0]["last_confirmed_at"]
    # 最后一轮跑在 clock 推进之前,所以「现在」距最后一次确认正好 1 天。
    assert last_confirmed != snapshots[0]["captured_at"]
    assert last_confirmed.startswith("2026-10-18")   # 起点 08-20 + 59 天
    assert snapshots[0]["last_confirmed_run_id"] == 60


def test_content_hash_ignores_numeric_spelling(tmp_path) -> None:
    """4300 / 4300.0 / "4300" 必须算出同一个哈希,否则每轮都被判成变更点。"""
    base = make_row("L1", monthly_rent_yuan=4300.0)
    digests = {
        content_hash(replace(base, monthly_rent_yuan=value))
        for value in (4300, 4300.0, "4300", "4300.00")
    }
    assert len(digests) == 1

    # None 与空串必须区分:一个是「上游没给」,一个是「上游给了空」。
    assert content_hash(replace(base, orientation=None)) != content_hash(
        replace(base, orientation="")
    )


def test_hash_excludes_fitment_status(tmp_path) -> None:
    """装修变了房源会直接从 fitment=002 的结果集消失,由 is_active=0 表达。"""
    base = make_row("L1")
    assert content_hash(base) == content_hash(replace(base, fitment_status="003"))


def test_repeated_listing_within_one_run_is_deduped(tmp_path) -> None:
    """上游翻页抖动会让同一 listing_id 跨页重复出现,取后见到的那次。"""
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, [make_row("L1", monthly_rent_yuan=4300.0)])
    repository.stage_rows(run_id, [make_row("L1", monthly_rent_yuan=4400.0)])
    repository.record_progress(run_id, pages_done=2, pages_expected=2)

    result = repository.complete_run(run_id)
    assert result["published"] == 1
    assert repository.get_current("L1")["monthly_rent_yuan"] == 4400.0


# ------------------------------------------------------------------ 时间字段


def test_first_seen_at_is_never_moved_forward(tmp_path) -> None:
    """4.4 规则 1:first_seen_at 只是「首次见到」的下界,不能被后续轮覆盖。"""
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    publish(repository, [make_row("L1")])
    first_seen = repository.get_current("L1")["first_seen_at"]

    clock.advance(days=3)
    publish(repository, [make_row("L1", monthly_rent_yuan=4500.0)])
    current = repository.get_current("L1")

    assert current["first_seen_at"] == first_seen
    assert current["last_seen_at"] != first_seen


def test_disappearing_listing_is_marked_inactive(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    publish(repository, [make_row("L1"), make_row("L2")])

    clock.advance(days=1)
    result = publish(repository, [make_row("L1")])

    assert result["deactivated"] == 1
    assert repository.get_current("L2")["is_active"] == 0
    assert repository.get_current("L1")["is_active"] == 1
    assert repository.active_count() == 1


def test_returning_listing_is_reactivated(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    publish(repository, [make_row("L1"), make_row("L2")])
    clock.advance(days=1)
    publish(repository, [make_row("L1")])
    clock.advance(days=1)
    publish(repository, [make_row("L1"), make_row("L2")])

    assert repository.get_current("L2")["is_active"] == 1
    assert repository.active_count() == 2


# ------------------------------------------------------------------ 装修三分


def test_unknown_and_non_roughcast_fitment_are_counted_not_dropped(tmp_path) -> None:
    """§七.8 / §八 U 的队列 A 一半:三分类计数,原值留在 stage,只有 002 进 current。"""
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    repository.stage_rows(run_id, [
        make_row("P1", fitment_status="002"),
        make_row("P2", fitment_status="002"),
        make_row("R1", fitment_status="001"),
        make_row("R2", fitment_status="003"),
        make_row("U1", fitment_status=None),
        make_row("U2", fitment_status=""),
    ])
    repository.record_progress(run_id, pages_done=1, pages_expected=1)
    result = repository.complete_run(run_id)

    run = repository.get_run(run_id)
    assert run["items_seen"] == 6
    assert run["non_roughcast_count"] == 2
    assert run["unknown_fitment_count"] == 2
    assert result["published"] == 2

    # 只有 002 进被排序集 P。
    assert repository.active_count() == 2
    for listing_id in ("R1", "R2", "U1", "U2"):
        assert repository.get_current(listing_id) is None

    # 但原值全部留在 stage,不得静默丢弃。
    assert repository.stage_count(run_id) == 6
    with repository.database.connect() as db:
        staged = {
            row["listing_id"]: row["fitment_status"]
            for row in db.execute(
                "SELECT listing_id, fitment_status FROM roughcast_crawl_stage "
                "WHERE run_id = ? AND listing_id IN ('U1', 'U2')", (run_id,)
            )
        }
    assert staged == {"U1": None, "U2": ""}


# ------------------------------------------------------------------ 小区


def test_communities_are_upserted_with_recomputed_counts(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    publish(repository, [
        make_row("L1", resblock_id="RB001", community_name="甲小区"),
        make_row("L2", resblock_id="RB001", community_name="甲小区"),
        make_row("L3", resblock_id="RB002", community_name="乙小区"),
    ])

    first = repository.community("RB001")
    assert first["roughcast_count"] == 2
    assert first["name"] == "甲小区"
    assert isinstance(first["resblock_id"], str)
    first_seen = first["first_seen_at"]

    clock.advance(days=1)
    publish(repository, [make_row("L1", resblock_id="RB001", community_name="甲小区")])

    updated = repository.community("RB001")
    assert updated["roughcast_count"] == 1          # 现算,不是累加
    assert updated["first_seen_at"] == first_seen   # 不被后续轮覆盖
    # 本轮没出现的小区必须归零,否则队列 B 会按过期计数排优先级。
    assert repository.community("RB002")["roughcast_count"] == 0


def test_community_key_falls_back_to_name_hash(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    publish(repository, [make_row("L1", resblock_id=None, community_name="无编号小区")])

    row = repository.get_current("L1")
    assert row["community_id"].startswith("name:")
    assert repository.community(row["community_id"])["roughcast_count"] == 1


def test_empty_publish_zeroes_all_community_counts(tmp_path) -> None:
    clock = FakeClock()
    repository = RoughcastRepository(make_db(tmp_path), clock=clock)
    publish(repository, [make_row("L1")])
    assert repository.community("RB001")["roughcast_count"] == 1

    clock.advance(days=1)
    publish(repository, [])

    assert repository.community("RB001")["roughcast_count"] == 0
    assert repository.active_count() == 0


# ------------------------------------------------------------------ 日志


def test_run_request_count_is_synced_from_the_log(tmp_path) -> None:
    repository = RoughcastRepository(make_db(tmp_path), clock=FakeClock())
    run_id = repository.start_run(QUEUE)
    for page in (1, 2, 3):
        repository.log_request(run_id=run_id, queue=QUEUE, target=f"page={page}", status="issued")

    assert repository.sync_run_request_count(run_id) == 3
    assert repository.get_run(run_id)["request_count"] == 3
