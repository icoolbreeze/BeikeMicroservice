from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.application.sale_archive import SaleArchiveRunner, _content_hash
from app.application.service import ConnectorService
from app.domain.errors import (
    ConnectorError,
    NetworkRequiredError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    ConnectionState,
    Principal,
    ProviderStatus,
    SaleAgentComment,
    SaleAgentCommentSection,
    SaleFollowRecord,
    SaleListing,
    SaleListingDetail,
    SaleListingFilters,
    SaleListingPage,
    SaleListingScore,
    SaleMaintainField,
    SaleMaintainInfo,
    SaleMaintainModule,
    SaleOwnerRecommend,
    SaleScoreDimension,
    SaleScoreDimensions,
    SaleScoreSection,
)
from app.infrastructure.sale_archive_plan import (
    DISTRICTS,
    SKIPPED_DISTRICT_IDS,
    ArchiveCursor,
    is_skipped_district,
)
from app.infrastructure.sale_archive_store import (
    ArchiveListingRow,
    SaleArchiveStore,
)
from app.infrastructure.settings import Settings

NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)  # 14:00 Asia/Shanghai

# SaleListingDetail carries ~60 upstream fields; only a few matter here.
_DETAIL_BLANKS = {
    item.name: None for item in fields(SaleListingDetail)
}


class StubSessionProvider:
    """Reports a local connection state; never performs a fetch."""

    def __init__(self, state: ConnectionState = ConnectionState.READY) -> None:
        self.state = state

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.state, "stub")

    def bound_principal(self) -> Principal | None:
        return Principal(employee_principal="employee-1")

    def authorized_fetch(self, request):
        raise AssertionError("tests must not reach the network")


@dataclass
class FakeSaleCrmClient:
    """Records every allowed call; raises on anything the archive must avoid."""

    listings: tuple[SaleListing, ...] = ()
    total: int = 1
    search_filters: list[SaleListingFilters] = field(default_factory=list)
    head_calls: list[str] = field(default_factory=list)
    maintain_calls: list[str] = field(default_factory=list)
    follow_calls: list[str] = field(default_factory=list)
    comment_calls: list[str] = field(default_factory=list)
    owner_calls: list[str] = field(default_factory=list)
    score_calls: list[str] = field(default_factory=list)
    # Listing ids whose detail head call the upstream rejects (400).
    detail_invalid_input_ids: set[str] = field(default_factory=set)
    # Optional blocks that the upstream rejects for one listing.
    comment_invalid_input_ids: set[str] = field(default_factory=set)
    # When set, the detail head call raises this (auth/network retry).
    detail_error: ConnectorError | None = None

    def whoami(self) -> Principal:
        return Principal(employee_principal="employee-1")

    def search_sale_listings(
        self, filters: SaleListingFilters
    ) -> SaleListingPage:
        self.search_filters.append(filters)
        return SaleListingPage(
            items=self.listings,
            page=filters.page,
            total=self.total,
            has_more=filters.page * 30 < self.total,
            request_id="req-1",
        )

    def get_sale_listing_detail(self, listing_id: str) -> SaleListing:
        raise AssertionError("sale archive must never call get_sale_listing_detail")

    def get_sale_listing_detail_head(
        self, listing_id: str
    ) -> SaleListingDetail:
        self.head_calls.append(listing_id)
        if listing_id in self.detail_invalid_input_ids:
            raise UpstreamInvalidInputError(
                f"stale or invisible listing {listing_id}"
            )
        if self.detail_error is not None:
            raise self.detail_error
        return SaleListingDetail(
            **{
                **_DETAIL_BLANKS,
                "listing_id": listing_id,
                "display_name": "万象城一期",
                "display_floor": "中楼层(共18层)",
                "owner_reserve_price": "120万",
            }
        )

    def get_sale_listing_maintain_info(
        self, listing_id: str
    ) -> SaleMaintainInfo:
        self.maintain_calls.append(listing_id)
        return SaleMaintainInfo(
            listing_id=listing_id,
            modules=(
                SaleMaintainModule(
                    name="看房信息",
                    fields=(
                        SaleMaintainField(
                            key="ownerPhone",
                            name="业主电话",
                            value="13800001111",
                            important=False,
                            comment=None,
                        ),
                        SaleMaintainField(
                            key="visit_time",
                            name="看房时间",
                            value="周末",
                            important=True,
                            comment=None,
                        ),
                    ),
                ),
            ),
            important_fields=(),
            complete_rate="9/9",
            last_update_time=None,
            remark=None,
        )

    def get_sale_listing_follows(
        self, listing_id: str
    ) -> tuple[SaleFollowRecord, ...]:
        self.follow_calls.append(listing_id)
        return (
            SaleFollowRecord(
                follow_id=1,
                content="客户已看房，有意向",
                creator_name="张三 13912345678",
                create_time="2026-09-01 10:00",
                on_top=False,
                remarks=None,
                follow_label="客户有意向",
                video_url=None,
            ),
        )

    def get_sale_listing_agent_comment(self, listing_id: str) -> SaleAgentComment:
        self.comment_calls.append(listing_id)
        if listing_id in self.comment_invalid_input_ids:
            raise UpstreamInvalidInputError(f"no comment for {listing_id}")
        return SaleAgentComment(
            comment_status="50",
            summary="一句话卖点",
            audit_text="审核通过，外网可见",
            commented_at="2026-10-18 21:15:05",
            sections=(
                SaleAgentCommentSection(
                    title="核心卖点",
                    comment_type="sellPoint",
                    content="交通方便，联系 13800001111",
                ),
                SaleAgentCommentSection(
                    title="周边配套",
                    comment_type="mate",
                    content="学校商场",
                ),
            ),
        )

    def get_sale_listing_owner_recommend(self, listing_id: str) -> SaleOwnerRecommend:
        self.owner_calls.append(listing_id)
        return SaleOwnerRecommend(content=None, picture_urls=())

    def get_sale_listing_score(self, listing_id: str) -> SaleListingScore:
        self.score_calls.append(listing_id)
        dimension = SaleScoreDimension(0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        return SaleListingScore(
            score=8.27,
            is_haofang=False,
            quota_score=0.0,
            quota_score_level=0,
            sections=(
                SaleScoreSection(title="客户热度", lines=("带看量6，排名第1",)),
                SaleScoreSection(title="业主意愿", lines=("急售",)),
            ),
            dimension_scores=SaleScoreDimensions(
                house=dimension,
                biz_average=dimension,
                biz_max=dimension,
                community_average=dimension,
                community_max=dimension,
            ),
        )


def _listing(
    listing_id: str,
    *,
    total_price: float = 1_200_000.0,
    community: str = "万象城一期",
    layout: str = "2-1-1",
    area: float = 80.0,
    surface: str | None = "https://img.ljcdn.com/a.jpg",
    quality_score: float | None = 88.5,
    visit_count: int | None = 5,
    maintain_percentage: int | None = 90,
    holder_level: str | None = "A",
) -> SaleListing:
    return SaleListing(
        listing_id=listing_id,
        community=community,
        biz_circle="东湖",
        layout=layout,
        area_sqm=area,
        total_price_yuan=total_price,
        total_price_text="120万",
        unit_price_yuan_per_sqm=15000.0,
        floor_desc="中楼层",
        floor_type="平层",
        orientation="南",
        tags=("随时可看",),
        visit_count_15d=visit_count,
        follow_up=True,
        create_time=None,
        maintainer_name="李四",
        maintainer_tag="A级",
        maintain_percentage=maintain_percentage,
        quality_score=quality_score,
        holder_level=holder_level,
        del_type=1,
        community_id="c-1",
        payment_mode="商品房",
        stat_function="普通住宅",
        subway_line=None,
        subway_station=None,
        vr_status=1,
        surface_image_url=surface,
        floor_plan_image_url=None,
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "upstream_profile": "unconfigured",
        "qr_login_auto_start": False,
        "sale_archive_enabled": True,
        "sale_archive_db_path": str(tmp_path / "sale_archive.sqlite3"),
        # Zero delays and an all-day window keep the tests deterministic.
        "sale_archive_list_delay_min_seconds": 0.0,
        "sale_archive_list_delay_max_seconds": 0.0,
        "sale_archive_detail_delay_min_seconds": 0.0,
        "sale_archive_detail_delay_max_seconds": 0.0,
        "sale_archive_pause_seconds_min": 0.0,
        "sale_archive_pause_seconds_max": 0.0,
        "sale_archive_window_start": "00:00",
        "sale_archive_window_end": "23:59",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _build(
    tmp_path: Path,
    client: FakeSaleCrmClient,
    *,
    provider: StubSessionProvider | None = None,
    now: datetime = NOW,
    **overrides: object,
) -> tuple[SaleArchiveRunner, SaleArchiveStore]:
    settings = _settings(tmp_path, **overrides)
    session = provider or StubSessionProvider()
    service = ConnectorService(
        settings=settings, session_provider=session, crm_client=client
    )
    store = SaleArchiveStore(settings.sale_archive_db_path)
    runner = SaleArchiveRunner(
        settings,
        service,
        store,
        sleep=lambda _seconds: None,
        now=lambda: now,
        uniform=lambda low, high: low,
    )
    return runner, store


def _seed(store: SaleArchiveStore, listing_id: str, district_id: str) -> None:
    """Insert an active row from an earlier run (run id 999)."""
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id=listing_id,
            district_id=district_id,
            price_band=None,
            del_type=1,
            community="旧小区",
            payload={"list": {"listing_id": listing_id}},
            content_hash="old-hash",
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )


def _seed_completed(store: SaleArchiveStore) -> None:
    """Insert a complete detail captured 1 day ago (inside TTL), matching
    ``_listing("l-1")``'s fingerprint."""
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id="l-1",
            district_id=DISTRICTS[0].district_id,
            price_band=None,
            del_type=1,
            community="万象城一期",
            payload={"list": {"listing_id": "l-1"}},
            content_hash=_content_hash(_listing("l-1")),
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )
    store.mark_detail(
        "l-1",
        status="complete",
        detail={
            "head": {"listing_id": "l-1"},
            "score": {},
            "agent_comment": {},
            "owner_recommend": {},
        },
        captured_at=NOW - timedelta(days=1),
    )


def test_new_listing_localizes_with_six_service_calls_once(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)

    runner.run_tick()  # list page
    tick = runner.run_tick()  # localized detail

    assert tick.did_work is True
    assert tick.detail_listing_id == "l-1"
    assert client.head_calls == ["l-1"]
    assert client.maintain_calls == ["l-1"]
    assert client.follow_calls == ["l-1"]
    assert client.comment_calls == ["l-1"]
    assert client.owner_calls == ["l-1"]
    assert client.score_calls == ["l-1"]
    stored = store.get_listing("l-1")
    assert stored.detail_status == "complete"
    assert stored.detail_captured_at == NOW

    # Sighted again: unchanged and inside TTL, no detail is spent.
    runner.run_tick()
    assert client.head_calls == ["l-1"]


def test_localized_detail_counts_seven_upstream_requests_but_one_detail(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)

    runner.run_tick()  # 1 list request
    # head(2) + maintain(1) + follows(1) + comment + owner + score = 7
    runner.run_tick()

    counters = store.daily_counters("2026-09-09")
    assert counters.request_count == 8
    assert counters.detail_count == 1
    run = store.latest_run()
    assert run.request_count == 8
    assert run.detail_count == 1


def test_detail_payload_stores_head_maintain_follows_stripped(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()

    detail = store.get_listing("l-1").payload["detail"]
    assert detail["head"]["listing_id"] == "l-1"
    assert detail["maintain"]["modules"][0]["name"] == "看房信息"
    assert detail["follows"][0]["content"] == "客户已看房，有意向"
    assert detail["agent_comment"]["comment_status"] == "50"
    assert detail["agent_comment"]["sections"][0]["title"] == "核心卖点"
    assert detail["agent_comment"]["sections"][0]["content"] == "交通方便，联系 "
    assert detail["agent_comment"]["sections"][1]["title"] == "周边配套"
    assert detail["owner_recommend"] == {"content": None, "picture_urls": []}
    assert detail["score"]["score"] == 8.27
    assert detail["score"]["is_haofang"] is False
    assert detail["score"]["sections"][0]["title"] == "客户热度"
    assert detail["score"]["sections"][0]["lines"] == ["带看量6，排名第1"]
    assert detail["score"]["sections"][1]["title"] == "业主意愿"

    raw = json.dumps(detail, ensure_ascii=False)
    # 业主预期价 gone, the phone in creator_name redacted, the ownerPhone
    # maintain row dropped.
    assert "owner_reserve_price" not in raw
    assert "120万" not in raw
    assert "13912345678" not in raw
    assert "业主电话" not in raw
    # Coarse floor text and the non-sensitive maintain row survive.
    assert "中楼层" in raw
    assert "看房时间" in raw
    assert "周末" in raw


def test_stale_detail_is_refetched_after_ttl(tmp_path: Path) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)
    runner.run_tick()
    runner.run_tick()
    assert client.head_calls == ["l-1"]

    later = NOW + timedelta(days=20)
    runner_later, _ = _build(tmp_path, client, now=later)
    runner_later.run_tick()
    runner_later.run_tick()

    assert client.head_calls == ["l-1", "l-1"]
    assert store.get_listing("l-1").detail_captured_at == later


def test_inside_ttl_sighting_without_fingerprint_change_spends_nothing(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)
    _seed_completed(store)

    runner.run_tick()

    assert client.head_calls == []
    assert store.get_listing("l-1").detail_status == "complete"


def test_completed_row_missing_score_is_backfilled_once(tmp_path: Path) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id="l-1",
            district_id=DISTRICTS[0].district_id,
            price_band=None,
            del_type=1,
            community="万象城一期",
            payload={"list": {"listing_id": "l-1"}},
            content_hash=_content_hash(_listing("l-1")),
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )
    store.mark_detail(
        "l-1",
        status="complete",
        detail={"head": {"listing_id": "l-1"}, "maintain": {}, "follows": []},
        captured_at=NOW - timedelta(days=1),
    )

    runner.run_tick()
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    assert client.score_calls == []

    runner.run_tick()
    assert client.score_calls == ["l-1"]
    detail = store.get_listing("l-1").payload["detail"]
    assert detail["score"]["sections"][0]["title"] == "客户热度"
    assert detail["score"]["sections"][0]["lines"] == ["带看量6，排名第1"]
    assert detail["agent_comment"]["sections"][0]["title"] == "核心卖点"
    assert detail["owner_recommend"] == {"content": None, "picture_urls": []}

    runner.run_tick()
    assert client.score_calls == ["l-1"]


def test_rejected_comment_stores_empty_object_and_keeps_the_listing(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(
        listings=(_listing("l-1"),),
        total=1,
        comment_invalid_input_ids={"l-1"},
    )
    runner, store = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()

    stored = store.get_listing("l-1")
    assert stored.detail_status == "complete"
    comment = stored.payload["detail"]["agent_comment"]
    assert comment["comment_status"] is None
    assert comment["sections"] == []
    assert stored.payload["detail"]["score"]["score"] == 8.27
    assert client.score_calls == ["l-1"]
    assert client.owner_calls == ["l-1"]


def test_fingerprint_ignores_quality_score_and_other_churn_fields() -> None:
    base = _listing("l-1")
    assert _content_hash(base) == _content_hash(
        replace(base, quality_score=60.0)
    )
    assert _content_hash(base) == _content_hash(
        replace(base, visit_count_15d=99)
    )
    assert _content_hash(base) == _content_hash(
        replace(base, maintain_percentage=10)
    )
    assert _content_hash(base) == _content_hash(
        replace(base, holder_level="S")
    )
    # A real fingerprint field change IS detected.
    assert _content_hash(base) != _content_hash(
        replace(base, total_price_yuan=1_300_000.0)
    )
    assert _content_hash(base) != _content_hash(
        replace(base, community="其他小区")
    )


def test_fingerprint_change_refreshes_detail_inside_ttl(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(
        listings=(_listing("l-1", total_price=1_300_000.0),), total=1
    )
    runner, _ = _build(tmp_path, client)
    # Old detail inside TTL but fingerprint (total price) now differs.
    client_total = _listing("l-1")

    # Seed directly through the same store the runner uses.
    settings = _settings(tmp_path)
    store = SaleArchiveStore(settings.sale_archive_db_path)
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id="l-1",
            district_id=DISTRICTS[0].district_id,
            price_band=None,
            del_type=1,
            community="万象城一期",
            payload={"list": {"listing_id": "l-1"}},
            content_hash=_content_hash(client_total),
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )
    store.mark_detail(
        "l-1",
        status="complete",
        detail={"head": {"listing_id": "l-1"}},
        captured_at=NOW - timedelta(days=1),
    )

    runner.run_tick()  # list sighting: fingerprint changed → queued
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    runner.run_tick()
    assert client.head_calls == ["l-1"]


def test_over_cap_district_splits_without_deactivating(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1500)
    runner, store = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    first = runner.run_tick()  # probe over cap → ABORTED, split to price grid
    assert first.did_work is True
    assert client.search_filters[0].scope == "all"
    assert client.search_filters[0].price_wan is None
    run = store.get_run(1)
    assert run.status == "ABORTED"
    assert run.abort_reason == "price_split"
    # Split did not census anything, so unseen listings stay active.
    assert store.get_listing("stale-1").is_active is True

    runner.run_tick()  # first price band page 1
    second_filters = client.search_filters[1]
    assert second_filters.price_wan == (0, 50)
    assert second_filters.page == 1
    assert store.get_listing("stale-1").is_active is True


def test_exhausted_unit_sweeps_reachable_rows_without_deactivating(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1500)
    runner, store = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)
    # 1-万 band, 1-㎡ area, single room: no split dimension remains.
    store.save_cursor(
        ArchiveCursor(
            district_index=0,
            price_band="100,101",
            extra_filters=(("area", "70,71"), ("room", "1,1")),
        ),
        NOW,
    )

    tick = runner.run_tick()

    assert len(client.search_filters) == 1
    filters = client.search_filters[0]
    assert filters.scope == "all"
    assert filters.price_wan == (100, 101)
    assert filters.area_sqm == (70, 71)
    assert filters.room_range == (1, 1)
    assert tick.cursor.page == 2
    assert tick.cursor.unit_resumed is True
    assert store.get_listing("stale-1").is_active is True
    run = store.latest_run()
    assert run.status == "RUNNING"
    assert run.abort_reason is None


def test_complete_unit_deactivates_unseen_listings(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    runner.run_tick()  # the single page completes the district

    assert store.get_listing("stale-1").is_active is False
    assert store.get_listing("l-1").is_active is True
    assert store.get_run(1).status == "COMPLETE"


def test_incomplete_unit_does_not_deactivate(tmp_path: Path) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=120)
    runner, store = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    runner.run_tick()  # page 1 of 4 — district not swept

    assert store.get_listing("stale-1").is_active is True
    assert store.latest_run().status == "RUNNING"


def test_resumed_unit_completes_without_deactivating(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)
    store.save_cursor(ArchiveCursor(unit_resumed=True), NOW)

    runner.run_tick()  # COMPLETE but resumed

    assert store.get_listing("stale-1").is_active is True
    assert store.get_run(1).status == "COMPLETE"
    assert store.get_run(1).abort_reason == "resumed_no_deactivate"


def test_disabled_runner_does_no_work(tmp_path: Path) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _ = _build(tmp_path, client, sale_archive_enabled=False)

    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "disabled"
    assert client.search_filters == []


def test_outside_window_does_nothing(tmp_path: Path) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _ = _build(
        tmp_path,
        client,
        sale_archive_window_start="09:30",
        sale_archive_window_end="10:00",
    )

    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "outside_window"
    assert client.search_filters == []


def test_auth_required_aborts_run_without_detail_calls(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=120)
    provider = StubSessionProvider()
    runner, store = _build(tmp_path, client, provider=provider)

    runner.run_tick()
    provider.state = ConnectionState.AUTH_REQUIRED
    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "auth_required"
    assert client.head_calls == []
    assert len(client.search_filters) == 1
    run = store.latest_run()
    assert run.status == "ABORTED"
    assert run.abort_reason == "auth_required"
    assert tick.cursor.unit_resumed is True


def test_auth_abort_keeps_pending_detail_queue_and_retries(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=120)
    provider = StubSessionProvider()
    runner, store = _build(tmp_path, client, provider=provider)

    runner.run_tick()  # page 1 queues l-1's detail
    provider.state = ConnectionState.AUTH_REQUIRED
    tick = runner.run_tick()

    assert tick.cursor.pending_detail_ids == ("l-1",)
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    assert client.head_calls == []

    provider.state = ConnectionState.READY
    runner.run_tick()
    assert client.head_calls == ["l-1"]
    assert store.load_cursor().pending_detail_ids == ()


def test_network_error_during_detail_keeps_the_in_flight_listing(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(
        listings=(_listing("l-1"),),
        total=120,
        detail_error=NetworkRequiredError("CRM upstream unreachable"),
    )
    runner, store = _build(tmp_path, client)

    runner.run_tick()
    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "upstream_error"
    assert tick.cursor.pending_detail_ids == ("l-1",)
    assert store.latest_run().status == "RUNNING"
    assert tick.cursor.unit_resumed is False

    client.detail_error = None
    runner.run_tick()
    assert client.head_calls == ["l-1", "l-1"]


def test_invalid_input_on_one_listing_does_not_block_queue(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(
        listings=(_listing("dead-a"), _listing("live-b")),
        total=2,
        detail_invalid_input_ids={"dead-a"},
    )
    runner, store = _build(tmp_path, client)

    runner.run_tick()
    first = runner.run_tick()  # dead-a rejected
    second = runner.run_tick()  # live-b localized

    assert first.detail_listing_id == "dead-a"
    assert second.detail_listing_id == "live-b"
    assert store.get_listing("dead-a").detail_status == "failed"
    assert store.get_listing("live-b").detail_status == "complete"
    assert client.head_calls == ["dead-a", "live-b"]
    assert store.daily_counters("2026-09-09").detail_count == 1


def test_status_lists_crawled_districts_with_zeros(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _ = _build(tmp_path, client)

    crawled_ids = [
        d.district_id for d in DISTRICTS if not is_skipped_district(d.district_id)
    ]
    empty = runner.status()
    assert len(empty.districts) == 14
    assert [row.district_id for row in empty.districts] == crawled_ids
    assert all(row.total == 0 for row in empty.districts)

    runner.run_tick()
    runner.run_tick()
    status = runner.status()
    assert status.listing_total == 1
    assert status.listing_complete == 1
    first = status.districts[0]
    assert first.district_name == "锦江" and first.complete == 1


def test_empty_district_probe_advances_to_next_district(
    tmp_path: Path,
) -> None:
    client = FakeSaleCrmClient(listings=(), total=0)
    runner, store = _build(tmp_path, client)

    runner.run_tick()

    assert store.load_cursor() == ArchiveCursor(district_index=1)


def test_default_window_is_working_hours_only() -> None:
    settings = Settings()
    assert settings.sale_archive_window_start == "09:00"
    assert settings.sale_archive_window_end == "21:00"
