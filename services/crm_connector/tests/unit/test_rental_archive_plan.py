from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.rental_archive_plan import (
    DISTRICTS,
    LIST_TOTAL_CAP,
    PRICE_BANDS,
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
    parse_price_band,
    split_current_unit,
    split_price_band,
)
from app.infrastructure.rental_archive_store import RentalArchiveStore

NOW = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)

# The district catalog before recursive splitting: order and ids are the
# live cursor's positional state, so they must stay exactly this.
ORIGINAL_12 = (
    ("锦江", "510104"),
    ("青羊", "510105"),
    ("金牛", "510106"),
    ("武侯", "510107"),
    ("成华", "510108"),
    ("龙泉驿", "510112"),
    ("双流", "510122"),
    ("温江", "510115"),
    ("新都", "510114"),
    ("郫都", "510124"),
    ("天府新区", "23008850"),
    ("高新西", "23009121"),
)

# Exact shape of district_cursor shipped before this change: no
# price_band / extra_filters / pending_units columns.
_LEGACY_CURSOR_DDL = """
CREATE TABLE district_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    district_index INTEGER NOT NULL,
    band_index INTEGER,
    page INTEGER NOT NULL,
    run_id INTEGER,
    seen_ids TEXT NOT NULL DEFAULT '[]',
    pending_detail_ids TEXT NOT NULL DEFAULT '[]',
    detail_run_id INTEGER,
    unit_total INTEGER,
    unit_resumed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


def test_district_catalog_keeps_first_12_and_appends_to_24() -> None:
    assert len(DISTRICTS) == 24
    assert tuple((d.name, d.district_id) for d in DISTRICTS[:12]) == ORIGINAL_12
    assert any(d.name == "高新" and d.district_id == "990002" for d in DISTRICTS)
    gaoxin = next(d for d in DISTRICTS if d.district_id == "990002")
    assert gaoxin.name == "高新"
    assert DISTRICTS.index(gaoxin) == 12  # directly after 高新西
    # Append-only tail, in the order the catalog was extended.
    tail = tuple((d.name, d.district_id) for d in DISTRICTS[12:])
    assert tail == (
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


def test_district_rotation_wraps_from_last_back_to_first() -> None:
    cursor = ArchiveCursor(district_index=len(DISTRICTS) - 1)
    advanced = next_unit(cursor)  # no pending units: rotate district
    assert advanced.district_index == 0
    assert advanced.price_band is None
    assert advanced.pending_units == ()


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


def test_next_unit_from_gaoxin_jumps_skipped_tail_to_tianfu_south() -> None:
    assert DISTRICTS[12].district_id == "990002"  # 高新
    advanced = next_unit(ArchiveCursor(district_index=12))
    assert advanced.district_index == 23  # 天府新区南区, not 13 青白江
    assert DISTRICTS[23].district_id == "23009222"
    assert advanced.price_band is None
    assert advanced.pending_units == ()


def test_next_unit_from_qingbaijiang_also_lands_on_tianfu_south() -> None:
    assert DISTRICTS[13].district_id == "510113"  # 青白江
    advanced = next_unit(ArchiveCursor(district_index=13))
    assert advanced.district_index == 23
    assert not is_skipped_district(DISTRICTS[advanced.district_index].district_id)


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


def test_split_closed_band_halves_without_invalid_or_duplicated_edges() -> None:
    left, right = split_price_band("1500:2000")
    lo_l, hi_l = parse_price_band(left)
    lo_r, hi_r = parse_price_band(right)
    assert lo_l < hi_l
    assert lo_r < hi_r
    assert lo_l == 1500 and hi_r == 2000
    assert hi_l == lo_r  # contiguous, no gap or overlap
    assert left == "1500:1750" and right == "1750:2000"


def test_split_open_high_band_keeps_second_child_open() -> None:
    left, right = split_price_band("8000:-1")
    lo_l, hi_l = parse_price_band(left)
    lo_r, hi_r = parse_price_band(right)
    assert lo_l == 8000 and hi_l == 16000
    assert lo_r == 16000 and hi_r == -1


def test_one_yuan_band_cannot_split_on_price() -> None:
    assert split_price_band("2000:2001") is None
    assert split_price_band("0:1") is None


def test_district_probe_over_cap_queues_initial_grid_starting_0_800() -> None:
    cursor = ArchiveCursor()
    assert needs_unit_split(cursor, 1500, page=1) is True
    split = split_current_unit(cursor)
    assert split.price_band == "0:800"
    assert split.extra_filters == ()
    assert tuple(unit.price_band for unit in split.pending_units) == PRICE_BANDS[1:]


def test_split_only_on_first_page_of_a_unit() -> None:
    cursor = ArchiveCursor(price_band="1500:2000")
    assert needs_unit_split(cursor, LIST_TOTAL_CAP + 1, page=1) is True
    assert needs_unit_split(cursor, LIST_TOTAL_CAP + 1, page=2) is False
    assert needs_unit_split(cursor, LIST_TOTAL_CAP, page=1) is False


def test_fat_band_splits_instead_of_sweeping_20_deep_pages() -> None:
    # 1500 hits / 50 per page would be 20 pages against the 1000-row wall.
    assert last_page(1500, 50) == 20
    cursor = ArchiveCursor(district_index=0, price_band="1500:2000")
    split = split_current_unit(cursor)
    assert split.price_band == "1500:1750"
    assert split.pending_units[0] == ArchiveUnit("1750:2000", ())
    # The next list request the runner builds uses the narrower band, page 1.
    filters = build_filters(
        district_id=cursor.district_id,
        price_band=split.price_band,
        page=split.page,
        page_size=50,
        extra_filters=split.extra_filters,
    )
    assert dict(filters.condition_filters) == {
        "districtId": "510104",
        "price": "1500:1750",
    }
    assert filters.page == 1


def test_one_yuan_band_over_cap_falls_back_to_deltype_split() -> None:
    cursor = ArchiveCursor(district_index=0, price_band="2000:2001")
    split = split_current_unit(cursor)
    assert split.price_band == "2000:2001"
    assert split.extra_filters == (("delType", "2"),)
    assert split.pending_units[0] == ArchiveUnit("2000:2001", (("delType", "5"),))
    filters = build_filters(
        district_id="510104",
        price_band=split.price_band,
        page=1,
        page_size=50,
        extra_filters=split.extra_filters,
    )
    assert dict(filters.condition_filters)["delType"] == "2"


def test_split_dimensions_end_at_one_yuan_band_plus_fixed_deltype() -> None:
    # Probe, wide band, and 1-yuan band without delType: all still splittable.
    assert can_split_unit(ArchiveCursor()) is True
    assert can_split_unit(ArchiveCursor(price_band="1500:2000")) is True
    assert can_split_unit(ArchiveCursor(price_band="2000:2001")) is True
    # After the delType split there is no dimension left; the runner must not
    # re-queue identical delType units in an endless split loop.
    exhausted = ArchiveCursor(
        price_band="2000:2001", extra_filters=(("delType", "2"),)
    )
    assert can_split_unit(exhausted) is False


def test_next_unit_drains_pending_units_before_changing_district() -> None:
    cursor = ArchiveCursor(
        district_index=3,
        price_band="0:800",
        pending_units=(
            ArchiveUnit("800:1200", ()),
            ArchiveUnit("1200:1500", (("delType", "2"),)),
        ),
    )
    first = next_unit(cursor)
    assert first.district_index == 3
    assert first.price_band == "800:1200"
    assert first.page == 1 and first.run_id is None
    second = next_unit(first)
    assert second.district_index == 3
    assert second.price_band == "1200:1500"
    assert second.extra_filters == (("delType", "2"),)
    third = next_unit(second)
    assert third.district_index == 4
    assert third.price_band is None
    assert third.extra_filters == ()
    assert third.pending_units == ()


def test_legacy_band_index_cursor_reconstructs_grid_queue(tmp_path: Path) -> None:
    # A live cloud DB from before this change: legacy table shape, mid-grid
    # cursor at band_index=5 (双流 is district_index=6 today).
    assert PRICE_BANDS[5] == "2500:3000"
    db_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(_LEGACY_CURSOR_DDL)
        connection.execute(
            "INSERT INTO district_cursor (id, district_index, band_index, page,"
            " run_id, seen_ids, pending_detail_ids, detail_run_id, unit_total,"
            " unit_resumed, updated_at)"
            " VALUES (1, 6, 5, 1, NULL, '[]', '[]', NULL, NULL, 0, ?)",
            (NOW.isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    store = RentalArchiveStore(str(db_path))
    cursor = store.load_cursor()
    assert cursor is not None
    assert cursor.district_index == 6
    assert cursor.price_band == "2500:3000"
    assert tuple(unit.price_band for unit in cursor.pending_units) == PRICE_BANDS[6:]
    assert all(unit.extra_filters == () for unit in cursor.pending_units)
    assert cursor.page == 1

    # Saving upgrades the row to the new shape; reload is stable and the
    # legacy int is kept readable for any old reader still alive.
    store.save_cursor(cursor, NOW)
    reloaded = store.load_cursor()
    assert reloaded == cursor
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT band_index, price_band, pending_units FROM district_cursor"
        ).fetchone()
    finally:
        connection.close()
    assert row[0] == 5
    assert row[1] == "2500:3000"
    assert "3000:4000" in row[2]


def test_recursive_cursor_round_trips_through_sqlite(tmp_path: Path) -> None:
    store = RentalArchiveStore(str(tmp_path / "roundtrip.sqlite3"))
    cursor = ArchiveCursor(
        district_index=12,
        price_band="2000:2001",
        extra_filters=(("delType", "5"),),
        pending_units=(
            ArchiveUnit("2000:2500", ()),
            ArchiveUnit("2500:3000", (("delType", "2"),)),
        ),
        page=3,
        pending_detail_ids=("l-9",),
        unit_total=1500,
    )
    store.save_cursor(cursor, NOW)
    assert store.load_cursor() == cursor


def test_legacy_unsliced_band_index_none_stays_a_probe(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_probe.sqlite3"
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(_LEGACY_CURSOR_DDL)
        connection.execute(
            "INSERT INTO district_cursor (id, district_index, band_index, page,"
            " run_id, seen_ids, pending_detail_ids, detail_run_id, unit_total,"
            " unit_resumed, updated_at)"
            " VALUES (1, 6, NULL, 1, NULL, '[]', '[]', NULL, NULL, 0, ?)",
            (NOW.isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    cursor = RentalArchiveStore(str(db_path)).load_cursor()
    assert cursor is not None
    assert cursor.price_band is None
    assert cursor.pending_units == ()
