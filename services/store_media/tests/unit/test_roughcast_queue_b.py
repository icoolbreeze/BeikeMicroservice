"""队列 B:小区参考集采集（§三 / §4.2）。"""

from __future__ import annotations

from datetime import UTC, datetime, time as dtime

import pytest

from app.application.roughcast_crawler import RoughcastCrawlConfig
from app.infrastructure.roughcast_crawl_client import CrawlRequestError
from app.infrastructure.roughcast_repository import (
    ABORTED,
    COMPLETE,
    FAILED,
    PARTIAL,
    RoughcastRepository,
)
from tests.roughcast_helpers import (
    FakeClock,
    FakeCrawlClient,
    make_db,
    make_queue_b_crawler,
    make_row,
    page_of,
    seed_community,
)


def config(**overrides) -> RoughcastCrawlConfig:
    base = {
        "page_size": 2,
        "page_gap_seconds": (0.0, 0.0),
        "long_pause_seconds": (0.0, 0.0),
        "min_request_interval_seconds": 0.0,
        "retry_reserve": 1,
    }
    return RoughcastCrawlConfig(**{**base, **overrides})


def test_run_once_publishes_all_fitments_and_does_not_touch_listing_current(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001", name="甲小区")
    # 队列 A 已经发布的清水房,队列 B 不得改它。
    listing_repo = RoughcastRepository(database, clock=FakeClock())
    run_a = listing_repo.start_run("A")
    listing_repo.stage_rows(run_a, [make_row("L1", resblock_id="RB001")])
    listing_repo.record_progress(run_a, pages_done=1, pages_expected=1)
    listing_repo.complete_run(run_a)
    before = dict(listing_repo.get_current("L1"))

    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of(
            [
                make_row("L1", resblock_id="RB001", fitment_status="002"),
                make_row("R1", resblock_id="RB001", fitment_status="003",
                         monthly_rent_yuan=8600.0, area_sqm=88.5),
                make_row("U1", resblock_id="RB001", fitment_status=None),
            ],
            page=1, total=3, has_more=False,
        )),
    ], page_size=2)
    outcome = make_queue_b_crawler(database, client, config=config()).run_once()

    assert outcome is not None
    assert outcome.status == COMPLETE
    assert outcome.reference_rows == 3
    assert outcome.reference_r_rows == 1
    assert client.requests == [("POST", "search:resblock=RB001&page=1")]
    assert "fitment" not in (client.search_bodies[0].get("condition_filters") or {})
    assert client.search_bodies[0]["resblock_ids"] == ["RB001"]
    assert client.search_bodies[0]["scope"] == "all"

    snapshots = listing_repo.reference_snapshot_for("RB001")
    by_id = {row["listing_id"]: row for row in snapshots}
    assert set(by_id) == {"L1", "R1", "U1"}
    assert by_id["L1"]["is_roughcast"] == 1
    assert by_id["R1"]["is_roughcast"] == 0
    assert by_id["U1"]["is_roughcast"] == 0
    assert by_id["U1"]["fitment_status"] is None
    assert by_id["R1"]["unit_rent"] == pytest.approx(8600.0 / 88.5)
    community = listing_repo.community("RB001")
    assert community["reference_run_id"] == outcome.run_id
    assert community["refresh_fail_count"] == 0

    after = dict(listing_repo.get_current("L1"))
    assert after["monthly_rent_yuan"] == before["monthly_rent_yuan"]
    assert after["last_seen_run_id"] == before["last_seen_run_id"]
    assert after["is_active"] == 1
    assert listing_repo.active_count() == 1


def test_pagination_fetches_every_community_page(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001")
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of([make_row("A1"), make_row("A2")], page=1, total=5, has_more=True)),
        ("RB001", page_of([make_row("A3"), make_row("A4")], page=2, total=5, has_more=True)),
        ("RB001", page_of([make_row("A5")], page=3, total=5, has_more=False)),
    ], page_size=2)

    outcome = make_queue_b_crawler(database, client, config=config()).run_once()

    assert outcome.status == COMPLETE
    assert outcome.pages_done == 3
    assert outcome.reference_rows == 5
    assert [target for _, target in client.requests] == [
        "search:resblock=RB001&page=1",
        "search:resblock=RB001&page=2",
        "search:resblock=RB001&page=3",
    ]


def test_aborted_run_does_not_advance_reference_pointer(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001")
    clock = FakeClock()
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of([make_row("A1")], page=1, total=1, has_more=False)),
    ], page_size=2)
    crawler = make_queue_b_crawler(
        database, client, clock=clock,
        config=config(window_start=dtime(9, 30), window_end=dtime(19, 0)),
    )
    clock.now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)  # 本地 20:00,窗外

    outcome = crawler.run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "window_closed"
    repo = RoughcastRepository(database)
    assert repo.community("RB001")["reference_run_id"] is None
    assert repo.reference_snapshot_for("RB001") == []


def test_partial_group_publishes_only_successful_communities(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001", name="甲", roughcast_count=3)
    seed_community(database, community_id="RB002", name="乙", roughcast_count=1)
    client = FakeCrawlClient(
        community_pages=[
            ("RB001", page_of([make_row("A1", resblock_id="RB001")], page=1, total=1, has_more=False)),
            ("RB002", page_of([make_row("B1", resblock_id="RB002")], page=1, total=3, has_more=True)),
        ],
        community_errors={
            ("RB002", 2): [CrawlRequestError("boom", http_status=503)],
        },
        page_size=2,
    )

    outcome = make_queue_b_crawler(database, client, config=config(retry_reserve=0)).run_once()

    assert outcome.status == PARTIAL
    repo = RoughcastRepository(database)
    assert repo.community("RB001")["reference_run_id"] == outcome.run_id
    assert repo.community("RB002")["reference_run_id"] is None
    assert repo.community("RB002")["refresh_fail_count"] == 1
    assert {row["listing_id"] for row in repo.reference_snapshot_for("RB001")} == {"A1"}
    assert repo.reference_snapshot_for("RB002") == []


def test_failed_community_increments_fail_count_and_stays_unreferenced(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001")
    client = FakeCrawlClient(
        community_pages=[
            ("RB001", page_of([make_row("A1")], page=1, total=3, has_more=True)),
        ],
        community_errors={("RB001", 2): [CrawlRequestError("boom", http_status=500)]},
        page_size=2,
    )

    outcome = make_queue_b_crawler(
        database, client, config=config(retry_reserve=0),
    ).run_sweep()

    assert outcome.communities_failed == 1
    assert outcome.communities_success == 0
    repo = RoughcastRepository(database)
    assert repo.community("RB001")["reference_run_id"] is None
    assert repo.community("RB001")["refresh_fail_count"] == 1


def test_sweep_skips_already_referenced_and_communities_without_resblock(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001", name="待刷新")
    seed_community(database, community_id="RB002", name="已有", reference_run_id=99)
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_communities "
            "(id, name, resblock_id, bizcircle, roughcast_count, first_seen_at) "
            "VALUES ('name:deadbeef', '无名', NULL, NULL, 1, '2026-08-20T03:00:00+00:00')"
        )
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of([make_row("A1", resblock_id="RB001")], page=1, total=1, has_more=False)),
        ("RB002", page_of([make_row("B1", resblock_id="RB002")], page=1, total=1, has_more=False)),
    ], page_size=2)

    outcome = make_queue_b_crawler(database, client, config=config()).run_sweep()

    assert outcome.communities_targeted == 1
    assert outcome.communities_success == 1
    assert [target for _, target in client.requests] == ["search:resblock=RB001&page=1"]
    repo = RoughcastRepository(database)
    assert repo.community("RB002")["reference_run_id"] == 99


def test_sweep_is_resume_safe_after_partial_progress(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001", name="甲", roughcast_count=2)
    seed_community(database, community_id="RB002", name="乙", roughcast_count=1)
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of([make_row("A1", resblock_id="RB001")], page=1, total=1, has_more=False)),
        ("RB002", page_of([make_row("B1", resblock_id="RB002")], page=1, total=1, has_more=False)),
    ], page_size=2)
    crawler = make_queue_b_crawler(database, client, config=config())

    first = crawler.run_sweep()
    assert first.communities_success == 2
    requests_after_first = list(client.requests)

    second = crawler.run_sweep()
    assert second.communities_targeted == 0
    assert client.requests == requests_after_first


def test_over_cap_community_is_not_published(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001")
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of(
            [make_row("A1")], page=1, total=1000, has_more=True,
        )),
    ], page_size=2)

    outcome = make_queue_b_crawler(database, client, config=config()).run_sweep()

    assert outcome.communities_failed == 1
    repo = RoughcastRepository(database)
    assert repo.community("RB001")["reference_run_id"] is None
    assert repo.reference_snapshot_for("RB001") == []


def test_queue_b_inventory_counts(tmp_path) -> None:
    database = make_db(tmp_path)
    seed_community(database, community_id="RB001")
    seed_community(database, community_id="RB002", reference_run_id=1)
    with database.connect() as db:
        db.execute(
            "INSERT INTO roughcast_communities "
            "(id, name, resblock_id, bizcircle, roughcast_count, first_seen_at) "
            "VALUES ('name:x', '占位', NULL, NULL, 0, '2026-08-20T03:00:00+00:00')"
        )
    inventory = RoughcastRepository(database).queue_b_inventory()
    assert inventory["communities"] == 3
    assert inventory["searchable"] == 2
    assert inventory["unreferenced"] == 1
    assert inventory["referenced"] == 1
    assert inventory["skipped_no_resblock"] == 1


def test_reference_snapshot_uses_pointer_not_max_run_id(tmp_path) -> None:
    """§4.1:读指针,不是 max(run_id)。"""
    database = make_db(tmp_path)
    clock = FakeClock()
    repo = RoughcastRepository(database, clock=clock)
    seed_community(database, community_id="RB001")
    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of([make_row("A1", resblock_id="RB001", monthly_rent_yuan=4000.0)],
                          page=1, total=1, has_more=False)),
    ], page_size=2)
    first = make_queue_b_crawler(database, client, clock=clock, config=config()).run_once()
    assert first.status == COMPLETE

    clock.advance(days=1)
    doomed = repo.start_run("B")
    repo.stage_rows(
        doomed,
        [make_row("A1", resblock_id="RB001", monthly_rent_yuan=1.0)],
        bucket="resblock=RB001",
    )
    repo.abort_run(doomed, "breaker_open")

    rows = repo.reference_snapshot_for("RB001")
    assert len(rows) == 1
    assert rows[0]["monthly_rent_yuan"] == 4000.0
    assert rows[0]["run_id"] == first.run_id
    assert repo.community("RB001")["reference_run_id"] == first.run_id


def test_score_source_becomes_self_after_reference_publish(tmp_path) -> None:
    database = make_db(tmp_path)
    clock = FakeClock()
    repo = RoughcastRepository(database, clock=clock)
    seed_community(database, community_id="RB001", bizcircle="华侨城")
    run_a = repo.start_run("A")
    repo.stage_rows(run_a, [make_row("L1", resblock_id="RB001", bizcircle="华侨城")])
    repo.record_progress(run_a, pages_done=1, pages_expected=1)
    repo.complete_run(run_a)
    assert repo.get_current("L1")["score_source"] == "self"  # 第 1 期占位:有小区就算 self

    client = FakeCrawlClient(community_pages=[
        ("RB001", page_of(
            [make_row("R1", resblock_id="RB001", fitment_status="003")],
            page=1, total=1, has_more=False,
        )),
    ], page_size=2)
    make_queue_b_crawler(database, client, clock=clock, config=config()).run_once()

    assert repo.get_current("L1")["score_source"] == "self"
    assert repo.community("RB001")["reference_run_id"] is not None
