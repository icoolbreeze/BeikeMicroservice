from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.application.rental_archive import RentalArchiveRunner, _content_hash
from app.application.service import ConnectorService
from app.domain.errors import (
    ConnectorError,
    NetworkRequiredError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    ConnectionState,
    FollowRecord,
    ListingDetailInfo,
    ListingMaintainInfo,
    ListingProspect,
    MaintainField,
    MaintainModule,
    Principal,
    ProspectPhoto,
    ProviderStatus,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
    TrusteeshipDetail,
)
from app.infrastructure.rental_archive_plan import (
    SKIPPED_DISTRICT_IDS,
    ArchiveCursor,
    DISTRICTS,
)
from app.infrastructure.rental_archive_store import ArchiveListingRow, RentalArchiveStore
from app.infrastructure.settings import Settings

NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)  # 14:00 Asia/Shanghai

# TrusteeshipDetail carries ~35 upstream fields; only a few matter here.
_TRUSTEESHIP_BLANKS = {
    item.name: ()
    if item.name in ("tags", "prospects", "house_type_images", "deal_details", "fee_groups")
    else None
    for item in fields(TrusteeshipDetail)
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
class FakeCrmClient:
    """Records every allowed call; raises on anything the archive must avoid."""

    listings: tuple[RentalListing, ...] = ()
    total: int = 1
    search_filters: list[RentalListingFilters] = field(default_factory=list)
    detail_calls: list[str] = field(default_factory=list)
    prospect_calls: list[str] = field(default_factory=list)
    house_info_calls: list[tuple[str, bool]] = field(default_factory=list)
    redirect_calls: list[str] = field(default_factory=list)
    trusteeship_calls: list[str] = field(default_factory=list)
    maintain_address: bool = False
    # Listing ids whose plain-detail head call the upstream rejects (400).
    detail_invalid_input_ids: set[str] = field(default_factory=set)
    # When set, the plain-detail head call raises this (auth/network retry).
    detail_error: ConnectorError | None = None

    def whoami(self) -> Principal:
        return Principal(employee_principal="employee-1")

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        self.search_filters.append(filters)
        return RentalListingPage(
            items=self.listings,
            page=filters.page,
            page_size=filters.page_size,
            total=self.total,
            has_more=filters.page * filters.page_size < self.total,
            request_id="req-1",
        )

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        self.detail_calls.append(listing_id)
        if listing_id in self.detail_invalid_input_ids:
            raise UpstreamInvalidInputError(f"stale or invisible listing {listing_id}")
        if self.detail_error is not None:
            raise self.detail_error
        return _listing(listing_id, del_type=2, floor_desc="中楼层", total_floors=18)

    def get_rental_listing_prospect(self, listing_id: str) -> ListingProspect:
        self.prospect_calls.append(listing_id)
        return ListingProspect(
            listing_id=listing_id,
            photos=(
                ProspectPhoto(
                    url="https://img.ljcdn.com/a.jpg",
                    room_name="主卧",
                    image_type="REAL",
                    upload_user="张三",
                    created_at=None,
                ),
            ),
            floor_plan_url=None,
            can_edit=True,
            has_survey_photo=True,
        )

    def get_rental_listing_house_info(
        self, listing_id: str, *, include_follows: bool = True
    ) -> ListingDetailInfo:
        self.house_info_calls.append((listing_id, include_follows))
        rows = [MaintainField(name="楼层", display_value="中楼层/18层", complete=True)]
        if self.maintain_address:
            rows.insert(
                0, MaintainField(name="详细地址", display_value="1栋2单元303", complete=True)
            )
            rows.insert(
                1, MaintainField(name="具体楼层", display_value="12层", complete=True)
            )
        follows = ()
        if include_follows:
            # The archive runs with include_follows=True and stores the records.
            follows = (
                FollowRecord(
                    content="客户已看房，钥匙在门店",
                    follow_type="普通跟进",
                    creator_name="李四",
                    role="维护人",
                    created_at=NOW,
                    labels=("真实在租",),
                    label_code="IN_RENT",
                    remarks=None,
                    on_top=True,
                    on_top_time=None,
                ),
            )
        return ListingDetailInfo(
            listing_id=listing_id,
            labels=("真实在租",),
            property_info=None,
            hqi=None,
            maintain=ListingMaintainInfo(
                listing_id=listing_id,
                modules=(MaintainModule(rate_text="9/9", fields=tuple(rows)),),
                remark="业主随时可看",
                all_field_rate=100,
                important_rate=100,
                owner_lowest_price="1800",
            ),
            follows=follows,
        )

    def get_rental_listing_redirect_url(self, listing_id: str) -> str | None:
        self.redirect_calls.append(listing_id)
        return "10612612882101"

    def get_trusteeship_detail(self, cell_code: str) -> TrusteeshipDetail:
        self.trusteeship_calls.append(cell_code)
        return TrusteeshipDetail(
            **{
                **_TRUSTEESHIP_BLANKS,
                "cell_code": cell_code,
                "house_del_code": "tg-1",
                "resblock_name": "望江橡树林",
                "floor_type": "中",
                "signal_floor": "12",
                "total_floor": 18,
                "guide_price_yuan": 2600,
            }
        )

    def get_rental_listing_follows(self, listing_id: str):
        raise AssertionError("archive must never request follow-up records")


def _listing(
    listing_id: str,
    *,
    del_type: int | None = 2,
    rent: float = 2600.0,
    community: str = "万象城一期",
    floor_desc: str | None = "中楼层",
    total_floors: int | None = 18,
    hqi_score: str | None = None,
) -> RentalListing:
    return RentalListing(
        listing_id=listing_id,
        community=community,
        layout="2室1厅",
        area_sqm=80.0,
        monthly_rent_yuan=rent,
        orientation="南",
        visible_scope="all",
        del_type=del_type,
        floor_desc=floor_desc,
        total_floors=total_floors,
        follow_total=12,
        follow_last_7d=3,
        hqi_score=hqi_score,
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "upstream_profile": "unconfigured",
        "qr_login_auto_start": False,
        "rental_archive_enabled": True,
        "rental_archive_db_path": str(tmp_path / "rental_archive.sqlite3"),
        # Zero delays and an all-day window keep the tests deterministic.
        "rental_archive_list_delay_min_seconds": 0.0,
        "rental_archive_list_delay_max_seconds": 0.0,
        "rental_archive_detail_delay_min_seconds": 0.0,
        "rental_archive_detail_delay_max_seconds": 0.0,
        "rental_archive_pause_seconds_min": 0.0,
        "rental_archive_pause_seconds_max": 0.0,
        "rental_archive_window_start": "00:00",
        "rental_archive_window_end": "23:59",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _build(
    tmp_path: Path,
    client: FakeCrmClient,
    *,
    provider: StubSessionProvider | None = None,
    now: datetime = NOW,
    **overrides: object,
) -> tuple[RentalArchiveRunner, RentalArchiveStore, list[float]]:
    settings = _settings(tmp_path, **overrides)
    session = provider or StubSessionProvider()
    service = ConnectorService(
        settings=settings, session_provider=session, crm_client=client
    )
    store = RentalArchiveStore(settings.rental_archive_db_path)
    slept: list[float] = []
    runner = RentalArchiveRunner(
        settings,
        service,
        store,
        sleep=slept.append,
        now=lambda: now,
        uniform=lambda low, high: low,
    )
    return runner, store, slept


def _seed(store: RentalArchiveStore, listing_id: str, district_id: str) -> None:
    """Insert an active row from an earlier run (run id 999)."""
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id=listing_id,
            district_id=district_id,
            price_band=None,
            del_type=2,
            community="旧小区",
            payload={"list": {"listing_id": listing_id}},
            content_hash="old-hash",
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )


def _seed_completed_plain(
    store: RentalArchiveStore, *, follows: object | None
) -> None:
    """Insert a complete plain detail captured 1 day ago (inside TTL).

    ``follows=None`` leaves the key out entirely — a pre-follows archive row;
    pass ``[]`` for the already-backfilled shape. The content hash matches
    ``_listing("l-1")`` so the list fingerprint is unchanged.
    """
    store.upsert_list_row(
        ArchiveListingRow(
            listing_id="l-1",
            district_id=DISTRICTS[0].district_id,
            price_band=None,
            del_type=2,
            community="万象城一期",
            payload={"list": {"listing_id": "l-1"}},
            content_hash=_content_hash(_listing("l-1")),
        ),
        run_id=999,
        now=NOW - timedelta(days=1),
    )
    house_info: dict[str, object] = {"listing_id": "l-1"}
    if follows is not None:
        house_info["follows"] = follows
    store.mark_detail(
        "l-1",
        status="complete",
        detail={"kind": "plain", "house_info": house_info},
        captured_at=NOW - timedelta(days=1),
    )


def test_localized_listing_skips_detail_calls(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()   # list page of district 0
    runner.run_tick()   # detail for the newly discovered listing
    assert client.detail_calls == ["l-1"]

    # The same listing surfaces again on the next district's page: already
    # localized, unchanged and inside the TTL, so no detail call is spent.
    runner.run_tick()
    assert client.detail_calls == ["l-1"]
    assert client.prospect_calls == ["l-1"]
    assert store.get_listing("l-1").detail_status == "complete"


def test_new_listing_fetches_allowed_detail_once(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()
    tick = runner.run_tick()

    assert tick.did_work is True
    assert tick.detail_listing_id == "l-1"
    assert client.detail_calls == ["l-1"]
    assert client.prospect_calls == ["l-1"]
    assert client.house_info_calls == [("l-1", True)]
    stored = store.get_listing("l-1")
    assert stored.detail_status == "complete"
    assert stored.detail_captured_at == NOW
    assert stored.payload["detail"]["kind"] == "plain"


def test_plain_detail_archives_follow_records(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()

    assert client.house_info_calls == [("l-1", True)]
    detail = store.get_listing("l-1").payload["detail"]
    follow = detail["house_info"]["follows"][0]
    assert follow["content"] == "客户已看房，钥匙在门店"
    assert follow["follow_type"] == "普通跟进"
    assert follow["creator_name"] == "李四"
    assert follow["on_top"] is True
    raw = json.dumps(detail, ensure_ascii=False)
    # Follow data is in; the quota-limited / sensitive fields stay out.
    for forbidden in (
        "具体楼层", "详细地址", "upload_user", "owner_lowest_price",
    ):
        assert forbidden not in raw


def test_old_plain_row_without_follows_is_backfilled_once(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    # Completed plain detail, inside TTL, unchanged fingerprint, no follows key.
    _seed_completed_plain(store, follows=None)

    runner.run_tick()  # list sighting → follow backfill queued
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    assert client.house_info_calls == []

    runner.run_tick()  # one detail refresh stores the follows
    assert client.house_info_calls == [("l-1", True)]
    detail = store.get_listing("l-1").payload["detail"]
    assert detail["house_info"]["follows"][0]["content"] == "客户已看房，钥匙在门店"

    # A second sighting inside TTL with the key present spends nothing.
    runner.run_tick()
    assert client.house_info_calls == [("l-1", True)]


def test_plain_row_with_empty_follows_is_not_refetched(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    _seed_completed_plain(store, follows=[])

    runner.run_tick()  # list sighting — ``follows: []`` counts as backfilled

    assert store.load_cursor().pending_detail_ids == ()
    assert client.house_info_calls == []
    assert client.detail_calls == []


def test_content_hash_ignores_list_hqi_score() -> None:
    # hqi_score is the search-row quality score; it must stay out of the
    # content hash, otherwise a changed score would refetch ~100k details.
    base = _listing("l-1")
    assert _content_hash(base) == _content_hash(replace(base, hqi_score="76"))
    assert _content_hash(base) == _content_hash(replace(base, hqi_score="0"))
    assert base.hqi_score is None and replace(base, hqi_score="76").hqi_score == "76"


def test_list_sighting_writes_hqi_score_without_detail_call(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1", hqi_score="76"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    # Completed plain detail inside the TTL, list fingerprint unchanged.
    _seed_completed_plain(store, follows=[])

    runner.run_tick()  # one list sighting

    payload = store.get_listing("l-1").payload
    # The upsert replaces the list object on every sighting...
    assert payload["list"]["hqi_score"] == "76"
    # ...and leaves the existing detail in place without a detail call.
    assert payload["detail"]["kind"] == "plain"
    assert payload["detail"]["house_info"]["listing_id"] == "l-1"
    assert client.detail_calls == []
    assert client.house_info_calls == []
    assert client.prospect_calls == []


def test_trusteeship_listing_uses_trusteeship_route_only(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("tg-1", del_type=5),), total=1)
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()

    assert client.redirect_calls == ["tg-1"]
    assert client.trusteeship_calls == ["10612612882101"]
    # 普租 detail routes are not served for del_type=5 and must not be tried.
    assert client.detail_calls == []
    assert client.prospect_calls == []
    assert client.house_info_calls == []
    raw = json.dumps(store.get_listing("tg-1").payload, ensure_ascii=False)
    assert "signalFloor" not in raw
    assert "signal_floor" not in raw
    assert "floor_type" in raw  # coarse floor data is kept


def test_district_over_deep_paging_cap_splits_into_price_bands(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1500)
    runner, _, _ = _build(tmp_path, client)

    first = runner.run_tick()
    assert first.did_work is True
    assert dict(client.search_filters[0].condition_filters) == {
        "districtId": DISTRICTS[0].district_id
    }

    runner.run_tick()
    assert dict(client.search_filters[1].condition_filters) == {
        "districtId": DISTRICTS[0].district_id,
        "price": "0:800",
    }


def test_small_district_is_not_split(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=10)
    runner, _, _ = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()
    runner.run_tick()

    assert all(
        "price" not in dict(filters.condition_filters)
        for filters in client.search_filters
    )


def test_incomplete_unit_does_not_deactivate(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=120)
    runner, store, _ = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    runner.run_tick()  # page 1 of 3 — the district is not swept yet

    assert store.get_listing("stale-1").is_active is True
    assert store.latest_run().status == "RUNNING"


def test_complete_unit_deactivates_unseen_listings(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    runner.run_tick()  # the single page completes the district

    assert store.get_listing("stale-1").is_active is False
    assert store.get_listing("l-1").is_active is True
    run = store.get_run(1)
    assert run.status == "COMPLETE"


def test_auth_required_aborts_run_without_detail_calls(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=120)
    provider = StubSessionProvider()
    runner, store, _ = _build(tmp_path, client, provider=provider)

    runner.run_tick()
    provider.state = ConnectionState.AUTH_REQUIRED
    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "auth_required"
    assert client.detail_calls == []
    assert client.house_info_calls == []
    assert len(client.search_filters) == 1
    run = store.latest_run()
    assert run.status == "ABORTED"
    assert run.abort_reason == "auth_required"
    # The cursor keeps its position but may no longer deactivate the unit.
    assert tick.cursor.unit_resumed is True


def test_daily_request_cap_stops_further_work(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=120)
    runner, _, _ = _build(tmp_path, client, rental_archive_daily_request_cap=1)

    first = runner.run_tick()
    second = runner.run_tick()

    assert first.did_work is True
    assert second.did_work is False
    assert second.skipped_reason == "daily_cap"
    assert len(client.search_filters) == 1
    assert client.detail_calls == []


def test_sensitive_fields_are_stripped_before_write(tmp_path: Path) -> None:
    client = FakeCrmClient(
        listings=(_listing("l-1"),), total=1, maintain_address=True
    )
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()
    runner.run_tick()

    raw = json.dumps(store.get_listing("l-1").payload, ensure_ascii=False)
    assert "详细地址" not in raw
    assert "具体楼层" not in raw
    assert "owner_lowest_price" not in raw
    assert "1栋2单元303" not in raw
    assert "中楼层" in raw  # 高/中/低 floor description survives


def test_outside_window_does_nothing(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _, _ = _build(
        tmp_path,
        client,
        rental_archive_window_start="09:30",
        rental_archive_window_end="10:00",
    )

    tick = runner.run_tick()

    assert tick.did_work is False
    assert tick.skipped_reason == "outside_window"
    assert client.search_filters == []


def test_disabled_runner_does_no_work(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _, _ = _build(tmp_path, client, rental_archive_enabled=False)

    tick = runner.run_tick()

    assert tick == tick  # dataclass identity keeps mypy quiet
    assert tick.did_work is False
    assert tick.skipped_reason == "disabled"
    assert client.search_filters == []


def test_stale_detail_is_refetched_after_ttl(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    runner.run_tick()
    runner.run_tick()
    assert client.detail_calls == ["l-1"]

    later = NOW + timedelta(days=20)
    runner_later, _, _ = _build(tmp_path, client, now=later)
    runner_later.run_tick()
    runner_later.run_tick()

    assert client.detail_calls == ["l-1", "l-1"]
    assert store.get_listing("l-1").detail_captured_at == later


def test_cursor_advances_through_districts(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(), total=0)
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()
    cursor = store.load_cursor()

    assert cursor == ArchiveCursor(district_index=1)
    assert runner.status().cursor.district.name == DISTRICTS[1].name


def test_status_lists_every_crawled_district_with_zeros(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _, _ = _build(tmp_path, client)

    crawled = [
        district for district in DISTRICTS
        if district.district_id not in SKIPPED_DISTRICT_IDS
    ]
    # On an empty store every *crawled* catalog district is planned, so 高新
    # (appended at index 12) shows up before its first row; the 10 far-suburban
    # skips (24 - 10 = 14) never appear.
    empty = runner.status()
    assert len(empty.districts) == len(DISTRICTS) - len(SKIPPED_DISTRICT_IDS)
    assert len(empty.districts) == 14
    assert [row.district_id for row in empty.districts] == [
        district.district_id for district in crawled
    ]
    gaoxin = next(row for row in empty.districts if row.district_id == "990002")
    assert gaoxin.district_name == "高新"
    assert gaoxin.total == gaoxin.complete == 0
    assert all(row.district_id != "510113" for row in empty.districts)

    runner.run_tick()
    runner.run_tick()
    status = runner.status()
    assert status.listing_total == 1
    assert status.listing_complete == 1
    assert status.listing_pending == 0
    assert len(status.districts) == 14
    assert all(row.district_id != "510113" for row in status.districts)
    row = next(
        row for row in status.districts
        if row.district_id == DISTRICTS[0].district_id
    )
    assert row.district_name == DISTRICTS[0].name
    assert row.complete == 1


def test_tick_on_skipped_district_jumps_without_calling_crm(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    assert DISTRICTS[13].district_id == "510113"  # 青白江
    # Stale cursor carrying the kind of state the skipped district must drop.
    store.save_cursor(
        ArchiveCursor(
            district_index=13,
            seen_ids=("ghost-1",),
            pending_detail_ids=("ghost-2",),
        ),
        NOW,
    )

    tick = runner.run_tick()

    assert tick.did_work is True
    assert tick.skipped_reason is None
    assert client.search_filters == []
    assert client.detail_calls == []
    # No run was opened or closed for a district never censused.
    assert store.latest_run() is None
    persisted = store.load_cursor()
    assert persisted.district_index == 23  # 天府新区南区
    assert persisted.price_band is None
    assert persisted.pending_units == ()
    assert persisted.seen_ids == ()
    assert persisted.pending_detail_ids == ()
    # The jump itself does not spend the daily request budget.
    assert store.daily_counters("2026-09-09").request_count == 0


def test_listing_query_filters_by_district_and_active(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client)
    _seed(store, "stale-1", DISTRICTS[0].district_id)

    runner.run_tick()

    active, total = store.list_listings(
        district_id=DISTRICTS[0].district_id, active=True
    )
    assert [item.listing_id for item in active] == ["l-1"]
    assert total == 1
    retired, retired_total = store.list_listings(
        district_id=DISTRICTS[0].district_id, active=False
    )
    assert [item.listing_id for item in retired] == ["stale-1"]
    assert retired_total == 1


def test_detail_daily_cap_keeps_list_sweep_running(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, store, _ = _build(tmp_path, client, rental_archive_detail_daily_cap=1)

    runner.run_tick()
    store.bump_daily("2026-09-09", details=1)
    tick = runner.run_tick()

    assert client.detail_calls == []
    assert tick.did_work is True
    assert len(client.search_filters) == 2


def test_zero_caps_are_unlimited(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _, _ = _build(
        tmp_path,
        client,
        rental_archive_daily_request_cap=0,
        rental_archive_detail_daily_cap=0,
    )

    assert runner.run_tick().did_work is True
    assert runner.run_tick().did_work is True
    assert client.detail_calls == ["l-1"]


def test_default_window_is_working_hours_only() -> None:
    """The shipped default must stay 09:00–21:00 Asia/Shanghai: crawl the
    working day, pause the night (21:00–09:00)."""
    settings = Settings()
    assert settings.rental_archive_window_start == "09:00"
    assert settings.rental_archive_window_end == "21:00"


def test_overnight_window_pauses_only_1am_to_9am(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    paused_at = datetime(2026, 9, 8, 17, 30, tzinfo=UTC)  # 01:30 Asia/Shanghai
    runner_paused, _, _ = _build(
        tmp_path,
        client,
        now=paused_at,
        rental_archive_window_start="09:00",
        rental_archive_window_end="01:00",
    )
    tick = runner_paused.run_tick()
    assert tick.did_work is False
    assert tick.skipped_reason == "outside_window"

    running_at = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    runner_open, _, _ = _build(
        tmp_path,
        client,
        now=running_at,
        rental_archive_window_start="09:00",
        rental_archive_window_end="01:00",
    )
    assert runner_open.run_tick().did_work is True


@pytest.mark.parametrize("state", [ConnectionState.READY, ConnectionState.EXPIRING])
def test_usable_session_states_allow_work(tmp_path: Path, state: ConnectionState) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1)
    runner, _, _ = _build(tmp_path, client, provider=StubSessionProvider(state))

    assert runner.run_tick().did_work is True


def test_auth_abort_keeps_pending_detail_queue_and_retries(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=120)
    provider = StubSessionProvider()
    runner, store, _ = _build(tmp_path, client, provider=provider)

    runner.run_tick()  # page 1 discovers l-1's pending detail
    provider.state = ConnectionState.AUTH_REQUIRED
    tick = runner.run_tick()  # would have fetched the detail; auth gone

    assert tick.skipped_reason == "auth_required"
    # The queue must survive both in memory and on disk: clearing it here is
    # what used to stall pending details until the next list sweep.
    assert tick.cursor.pending_detail_ids == ("l-1",)
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    assert client.detail_calls == []

    # Once the session is back the same queue is retried, not discarded.
    provider.state = ConnectionState.READY
    runner.run_tick()
    assert client.detail_calls == ["l-1"]
    assert store.load_cursor().pending_detail_ids == ()


def test_network_error_during_detail_keeps_the_in_flight_listing(
    tmp_path: Path,
) -> None:
    client = FakeCrmClient(
        listings=(_listing("l-1"),),
        total=120,  # multi-page unit so the discovering run stays open
        detail_error=NetworkRequiredError("CRM upstream unreachable"),
    )
    provider = StubSessionProvider()
    runner, store, _ = _build(tmp_path, client, provider=provider)

    runner.run_tick()  # page 1 discovers l-1's pending detail
    tick = runner.run_tick()  # detail fetch hits a transport failure

    assert tick.did_work is False
    assert tick.skipped_reason == "upstream_error"
    # The in-flight id was not consumed: it retries when the upstream is back.
    assert tick.cursor.pending_detail_ids == ("l-1",)
    assert store.load_cursor().pending_detail_ids == ("l-1",)
    # A transient network failure must not close the run or mark the unit
    # resumed: the same in-flight tick is retried after the 60s back-off.
    run = store.latest_run()
    assert run.status == "RUNNING"
    assert run.abort_reason is None
    assert tick.cursor.run_id is not None
    assert tick.cursor.unit_resumed is False

    client.detail_error = None
    runner.run_tick()
    # The failed attempt plus the successful retry both reached the head call.
    assert client.detail_calls == ["l-1", "l-1"]
    assert store.get_listing("l-1").detail_status == "complete"


def test_invalid_input_on_one_listing_does_not_block_detail_queue(
    tmp_path: Path,
) -> None:
    client = FakeCrmClient(
        listings=(_listing("dead-a"), _listing("live-b")),
        total=2,
        detail_invalid_input_ids={"dead-a"},
    )
    runner, store, _ = _build(tmp_path, client)

    runner.run_tick()  # one page queues [dead-a, live-b]
    first = runner.run_tick()  # dead-a: upstream 400
    second = runner.run_tick()  # live-b: normal detail

    assert first.did_work is True
    assert first.detail_listing_id == "dead-a"
    assert second.detail_listing_id == "live-b"
    # The bad listing is marked failed and logged; the rest of the queue ran.
    assert store.get_listing("dead-a").detail_status == "failed"
    assert store.get_listing("live-b").detail_status == "complete"
    assert client.detail_calls == ["dead-a", "live-b"]
    assert client.prospect_calls == ["live-b"]
    # Failed rows spend no detail budget and leave an audit log entry.
    assert store.daily_counters("2026-09-09").detail_count == 1
    errors = [entry for entry in store.log_entries(limit=20) if entry["status"] == "error"]
    assert any(entry["listing_id"] == "dead-a" for entry in errors)


def test_fat_price_band_splits_instead_of_sweeping_deep_pages(tmp_path: Path) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1500)
    runner, store, _ = _build(tmp_path, client)
    # Resume a cursor already slicing 1500:2000 (a static grid band that
    # overflows the 1000-row cap on its own).
    store.save_cursor(
        ArchiveCursor(district_index=0, price_band="1500:2000"), NOW
    )

    runner.run_tick()  # page-1 probe of the fat band → split, rows not swept
    cursor_after_split = store.load_cursor()
    runner.run_tick()  # first child band page 1

    assert cursor_after_split.price_band == "1500:1750"
    prices = [
        dict(filters.condition_filters).get("price")
        for filters in client.search_filters
    ]
    assert prices == ["1500:2000", "1500:1750"]
    # Nothing was paginated into the wall: both requests are page 1.
    assert [filters.page for filters in client.search_filters] == [1, 1]
    # The aborted fat-band probe wrote no census and split no run as COMPLETE.
    run = store.get_run(1)
    assert run.abort_reason == "price_split"
    assert run.items_seen == 0


def test_unit_over_cap_with_no_split_left_sweeps_without_deactivating(
    tmp_path: Path,
) -> None:
    client = FakeCrmClient(listings=(_listing("l-1"),), total=1500)
    runner, store, _ = _build(tmp_path, client)
    # A 1-yuan band that already carries the delType split: dimensions gone.
    store.save_cursor(
        ArchiveCursor(
            district_index=0,
            price_band="2000:2001",
            extra_filters=(("delType", "2"),),
        ),
        NOW,
    )

    tick = runner.run_tick()

    # No split happens (no endless identical delType re-queue): rows are
    # swept, page advances, and the unit is marked unfit to deactivate.
    assert len(client.search_filters) == 1
    filters = dict(client.search_filters[0].condition_filters)
    assert filters == {"districtId": "510104", "price": "2000:2001", "delType": "2"}
    assert tick.cursor.page == 2
    assert tick.cursor.unit_resumed is True
    assert tick.cursor.price_band == "2000:2001"
    assert tick.cursor.extra_filters == (("delType", "2"),)
    assert store.get_listing("l-1") is not None
    run = store.get_run(1)
    assert run.status == "RUNNING"
    assert run.abort_reason is None
