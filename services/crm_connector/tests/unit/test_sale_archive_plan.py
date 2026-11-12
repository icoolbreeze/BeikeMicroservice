from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.sale_archive_plan import (
    AREA_BANDS,
    DISTRICTS,
    LIST_TOTAL_CAP,
    PAGE_SIZE,
    PRICE_BANDS,
    ROOM_BANDS,
    SKIPPED_DISTRICT_IDS,
    ArchiveCursor,
    ArchiveUnit,
    build_filters,
    can_split_unit,
    is_skipped_district,
    last_page,
    needs_unit_split,
    next_crawled_district_index,
    next_unit,
    parse_band,
    split_area_band,
    split_current_unit,
    split_price_band,
    unit_signature,
)
from app.infrastructure.sale_archive_store import SaleArchiveStore

NOW = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)

# The catalog before this change: a live cursor stores its position as an
# index into DISTRICTS, so the first five entries must stay exactly this and
# the catalog may only be appended to.
ORIGINAL_FIVE = (
    ("锦江", "510104"),
    ("青羊", "510105"),
    ("金牛", "510106"),
    ("武侯", "510107"),
    ("成华", "510108"),
)


def test_district_catalog_keeps_first_five_and_appends_to_24() -> None:
    assert len(DISTRICTS) == 24
    assert tuple((d.name, d.district_id) for d in DISTRICTS[:5]) == ORIGINAL_FIVE
    # Same append-only tail, same names and ids, as the rental catalog.
    tail = tuple((d.name, d.district_id) for d in DISTRICTS[5:])
    assert tail == (
        ("龙泉驿", "510112"),
        ("双流", "510122"),
        ("温江", "510115"),
        ("新都", "510114"),
        ("郫都", "510124"),
        ("天府新区", "23008850"),
        ("高新西", "23009121"),
        ("高新", "990002"),
        ("青白江", "510113"),
        ("新津", "510132"),
        ("都江堰", "510181"),
        ("简阳", "23009119"),
        ("金堂", "510121"),
        ("崇州", "510184"),
        ("彭州", "510182"),
        ("大邑", "510129"),
        ("邛崃", "510183"),
        ("蒲江", "510131"),
        ("天府新区南区", "23009222"),
    )
    # 不限 (-1) is not a crawled district.
    assert all(d.district_id != "-1" for d in DISTRICTS)


def test_skipped_district_set_is_exactly_the_ten_far_suburbs() -> None:
    assert SKIPPED_DISTRICT_IDS == frozenset({
        "510113", "510132", "510181", "23009119", "510121",
        "510184", "510182", "510129", "510183", "510131",
    })
    assert len(SKIPPED_DISTRICT_IDS) == 10
    # 主城/近郊 + 天府新区 / 高新西 / 高新 / 天府新区南区 stay crawled.
    for kept in ("23008850", "23009121", "990002", "23009222"):
        assert not is_skipped_district(kept)
    assert len(DISTRICTS) - len(SKIPPED_DISTRICT_IDS) == 14


def test_district_rotation_wraps_from_last_back_to_first() -> None:
    cursor = ArchiveCursor(district_index=len(DISTRICTS) - 1)
    advanced = next_unit(cursor)  # no pending units: rotate district
    assert advanced.district_index == 0
    assert advanced.price_band is None
    assert advanced.pending_units == ()


def test_finishing_chenghua_advances_to_longquanyi_not_back_to_jinjiang() -> None:
    assert DISTRICTS[4].district_id == "510108"  # 成华
    assert DISTRICTS[5].district_id == "510112"  # 龙泉驿
    advanced = next_unit(ArchiveCursor(district_index=4))
    assert advanced.district_index == 5
    assert advanced.price_band is None
    assert advanced.pending_units == ()


def test_next_unit_from_gaoxin_jumps_skipped_tail_to_tianfu_south() -> None:
    assert DISTRICTS[12].district_id == "990002"  # 高新
    advanced = next_unit(ArchiveCursor(district_index=12))
    assert advanced.district_index == 23  # 天府新区南区, not 13 青白江
    assert DISTRICTS[23].district_id == "23009222"
    assert advanced.price_band is None
    assert advanced.pending_units == ()


def test_next_crawled_district_index_walks_past_every_skip() -> None:
    # Each skipped tail index (13..22) resolves to 天府新区南区 (23).
    for index in range(13, 23):
        assert is_skipped_district(DISTRICTS[index].district_id)
        assert next_crawled_district_index(index) == 23
    # 高新 (12) also jumps the whole skipped tail.
    assert next_crawled_district_index(12) == 23
    # 天府新区南区 wraps back to 锦江, and crawled head indices step by one.
    assert next_crawled_district_index(23) == 0
    assert next_crawled_district_index(0) == 1


def test_page_size_is_fixed_at_30() -> None:
    assert PAGE_SIZE == 30
    # 1000 reachable rows at 30 per page = 34 pages; totals above the cap do
    # not add pages.
    assert last_page(1000) == 34
    assert last_page(1500) == 34
    assert last_page(1) == 1


def test_split_closed_band_halves_without_invalid_or_duplicated_edges() -> None:
    left, right = split_price_band("90,120")
    lo_l, hi_l = parse_band(left)
    lo_r, hi_r = parse_band(right)
    assert lo_l < hi_l
    assert lo_r < hi_r
    assert lo_l == 90 and hi_r == 120
    assert hi_l == lo_r  # contiguous, no gap or overlap
    assert left == "90,105" and right == "105,120"


def test_split_open_high_price_band_keeps_second_child_open() -> None:
    left, right = split_price_band("500,-1")
    assert left == "500,1000"
    lo_r, hi_r = parse_band(right)
    assert lo_r == 1000 and hi_r == -1
    # The open child splits again the same way (not at a fixed 1000 cut).
    assert split_price_band("1000,-1") == ("1000,2000", "2000,-1")
    # Small open bands grow by max(100, lo).
    assert split_price_band("0,-1") == ("0,100", "100,-1")


def test_one_wan_band_cannot_split_on_price() -> None:
    assert split_price_band("100,101") is None
    assert split_price_band("0,1") is None


def test_district_probe_over_cap_queues_initial_price_grid() -> None:
    cursor = ArchiveCursor()
    assert needs_unit_split(cursor, 1500, page=1) is True
    split = split_current_unit(cursor)
    assert split.price_band == "0,50"
    assert split.extra_filters == ()
    assert tuple(unit.price_band for unit in split.pending_units) == PRICE_BANDS[1:]
    assert "0,-1" not in PRICE_BANDS  # 不限 skipped


def test_split_only_on_first_page_of_a_unit() -> None:
    cursor = ArchiveCursor(price_band="90,120")
    assert needs_unit_split(cursor, LIST_TOTAL_CAP + 1, page=1) is True
    assert needs_unit_split(cursor, LIST_TOTAL_CAP + 1, page=2) is False
    assert needs_unit_split(cursor, LIST_TOTAL_CAP, page=1) is False


def test_fat_price_band_splits_instead_of_sweeping_deep_pages() -> None:
    cursor = ArchiveCursor(district_index=0, price_band="90,120")
    split = split_current_unit(cursor)
    assert split.price_band == "90,105"
    assert split.pending_units[0] == ArchiveUnit("105,120", ())
    filters = build_filters(
        district_id=cursor.district_id,
        price_band=split.price_band,
        page=split.page,
        extra_filters=split.extra_filters,
    )
    assert filters.scope == "all"
    assert filters.district_id == "510104"
    assert filters.price_wan == (90, 105)
    assert filters.page == 1


def test_one_wan_band_over_cap_falls_back_to_area_grid() -> None:
    cursor = ArchiveCursor(district_index=0, price_band="100,101")
    split = split_current_unit(cursor)
    # Price kept; area grid queued.
    assert split.price_band == "100,101"
    assert split.extra_filters == (("area", "0,50"),)
    assert tuple(unit.price_band for unit in split.pending_units) == (
        "100,101",
    ) * (len(AREA_BANDS) - 1)
    assert tuple(
        unit.extra_filters[0] for unit in split.pending_units
    ) == tuple(("area", band) for band in AREA_BANDS[1:])


def test_split_closed_area_band_halves_contiguously() -> None:
    left, right = split_area_band("70,90")
    assert left == "70,80" and right == "80,90"


def test_split_open_high_area_band_keeps_second_child_open() -> None:
    assert split_area_band("200,-1") == ("200,400", "400,-1")
    assert split_area_band("0,-1") == ("0,50", "50,-1")


def test_one_sqm_area_band_cannot_split() -> None:
    assert split_area_band("70,71") is None
    assert split_area_band("0,1") is None


def test_exhausted_area_band_falls_back_to_rooms() -> None:
    cursor = ArchiveCursor(
        district_index=0,
        price_band="100,101",
        extra_filters=(("area", "70,71"),),
    )
    split = split_current_unit(cursor)
    assert split.price_band == "100,101"
    assert split.extra_filters == (("area", "70,71"), ("room", "1,1"))
    queued = tuple(
        unit.extra_filters[-1] for unit in split.pending_units
    )
    assert queued == (
        ("room", "2,2"),
        ("room", "3,3"),
        ("room", "4,4"),
        ("room", "5,-1"),
    )
    # The open 5+ band must not be accompanied by a 5,5 row.
    assert ("room", "5,5") not in queued


def test_room_grid_is_five_bands_without_a_5_5_slice() -> None:
    assert ROOM_BANDS == ("1,1", "2,2", "3,3", "4,4", "5,-1")
    assert "5,5" not in ROOM_BANDS


def test_build_filters_maps_price_area_and_room_to_sale_filters() -> None:
    filters = build_filters(
        district_id="510108",
        price_band="500,-1",
        page=2,
        extra_filters=(("area", "200,-1"), ("room", "5,-1")),
    )
    assert filters.scope == "all"
    assert filters.district_id == "510108"
    assert filters.price_wan == (500, None)
    assert filters.area_sqm == (200, None)
    assert filters.room_range == (5, -1)
    assert filters.rooms == ()
    assert filters.page == 2


def test_build_filters_without_band_is_unconstrained_probe() -> None:
    filters = build_filters(
        district_id="510104", price_band=None, page=1
    )
    assert filters.scope == "all"
    assert filters.price_wan is None
    assert filters.area_sqm is None
    assert filters.room_range is None


def test_split_dimensions_end_at_price_area_single_room() -> None:
    assert can_split_unit(ArchiveCursor()) is True
    assert can_split_unit(ArchiveCursor(price_band="90,120")) is True
    assert can_split_unit(ArchiveCursor(price_band="100,101")) is True
    assert can_split_unit(
        ArchiveCursor(price_band="100,101", extra_filters=(("area", "70,90"),))
    ) is True
    assert can_split_unit(
        ArchiveCursor(
            price_band="100,101", extra_filters=(("area", "70,71"),)
        )
    ) is True
    exhausted = ArchiveCursor(
        price_band="100,101",
        extra_filters=(("area", "70,71"), ("room", "1,1")),
    )
    assert can_split_unit(exhausted) is False


def test_next_unit_drains_pending_units_before_changing_district() -> None:
    cursor = ArchiveCursor(
        district_index=3,
        price_band="0,50",
        pending_units=(
            ArchiveUnit("50,70", (("area", "0,50"),)),
            ArchiveUnit("50,70", (("area", "0,50"), ("room", "5,-1"))),
        ),
    )
    first = next_unit(cursor)
    assert first.district_index == 3
    assert first.price_band == "50,70"
    assert first.extra_filters == (("area", "0,50"),)
    assert first.page == 1 and first.run_id is None
    second = next_unit(first)
    assert second.extra_filters == (("area", "0,50"), ("room", "5,-1"))
    third = next_unit(second)
    assert third.district_index == 4
    assert third.price_band is None
    assert third.pending_units == ()


def test_unit_signature_encodes_full_unit() -> None:
    assert unit_signature(None, ()) is None
    assert unit_signature("90,120", ()) == "90,120"
    assert unit_signature("90,120", (("area", "70,90"),)) == "90,120|area=70,90"
    assert (
        unit_signature("90,120", (("area", "70,90"), ("room", "5,-1")))
        == "90,120|area=70,90|room=5,-1"
    )


def test_cursor_round_trips_through_sqlite(tmp_path: Path) -> None:
    store = SaleArchiveStore(str(tmp_path / "roundtrip.sqlite3"))
    cursor = ArchiveCursor(
        district_index=3,
        price_band="100,101",
        extra_filters=(("area", "70,71"), ("room", "2,2")),
        pending_units=(
            ArchiveUnit("100,101", (("area", "70,71"), ("room", "3,3"))),
            ArchiveUnit("100,101", (("area", "70,71"), ("room", "5,-1"))),
        ),
        page=3,
        pending_detail_ids=("l-9",),
        unit_total=1500,
        unit_resumed=True,
    )
    store.save_cursor(cursor, NOW)
    assert store.load_cursor() == cursor
