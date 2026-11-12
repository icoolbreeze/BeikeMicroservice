"""Static crawl plan for the rental archive: districts, price bands, cursor math.

The archive walks the 全部房源 list the way an employee does: one Chengdu
administrative district at a time, page by page. A district (or a price band)
whose first page reports more hits than the upstream deep-paging cap is split
into smaller units — first by rent, then (for a 1-yuan band that still
overflows) by 普租/托管 delType — so every listing stays reachable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.domain.models import RentalListingFilters

MAX_PAGE_SIZE = 50
LIST_SCOPE = "all"

# The 房源列表 upstream stops serving rows past 1000 hits (deep-paging cap
# measured 2026-08-21, run/probe_deep_paging.py), so a unit above it is
# unreachable in one sweep and must be sliced.
LIST_TOTAL_CAP = 1000


@dataclass(frozen=True)
class District:
    """One Chengdu administrative district as the CRM identifies it."""

    name: str
    district_id: str


# Verified against the live 房源列表 districtId filter catalog. The first 12
# entries are the original catalog order (a live cursor stores its position as
# an index into this tuple) — append only, never reorder.
DISTRICTS: tuple[District, ...] = (
    District("锦江", "510104"),
    District("青羊", "510105"),
    District("金牛", "510106"),
    District("武侯", "510107"),
    District("成华", "510108"),
    District("龙泉驿", "510112"),
    District("双流", "510122"),
    District("温江", "510115"),
    District("新都", "510114"),
    District("郫都", "510124"),
    District("天府新区", "23008850"),
    District("高新西", "23009121"),
    District("高新", "990002"),
    District("青白江", "510113"),
    District("新津", "510132"),
    District("都江堰", "510181"),
    District("简阳", "23009119"),
    District("金堂", "510121"),
    District("崇州", "510184"),
    District("彭州", "510182"),
    District("大邑", "510129"),
    District("邛崃", "510183"),
    District("蒲江", "510131"),
    District("天府新区南区", "23009222"),
)

# Far suburban / county districts are out of rental localization scope: the
# crawl jumps over them (separate from DISTRICTS, which stays append-only).
SKIPPED_DISTRICT_IDS: frozenset[str] = frozenset({
    "510113", "510132", "510181", "23009119", "510121",
    "510184", "510182", "510129", "510183", "510131",
})

# Colon-separated monthly-rent bands, the only shape the upstream ``price``
# condition accepts (separate priceMin/priceMax params are silently ignored).
# This is the *initial* grid; fat bands are recursively halved at runtime.
PRICE_BANDS: tuple[str, ...] = (
    "0:800", "800:1200", "1200:1500", "1500:2000", "2000:2500", "2500:3000",
    "3000:4000", "4000:5000", "5000:6000", "6000:8000", "8000:-1",
)


@dataclass(frozen=True)
class ArchiveUnit:
    """One reachable slice of a district: an optional rent band plus optional
    extra list conditions (delType when a 1-yuan band still overflows)."""

    price_band: str | None
    extra_filters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ArchiveCursor:
    """Where the district sweep stands, persisted between ticks.

    A *unit* is one district plus at most one price band (and optionally a
    delType condition); only a unit that finished every page may deactivate
    listings it did not see. ``price_band is None`` with no extra filters is
    the unsliced first-page probe of a district.
    """

    district_index: int = 0
    price_band: str | None = None
    extra_filters: tuple[tuple[str, str], ...] = ()
    pending_units: tuple[ArchiveUnit, ...] = field(default=())
    """Remaining units for the current district; the fields above are the
    *current* unit."""
    page: int = 1
    run_id: int | None = None
    seen_ids: tuple[str, ...] = ()
    pending_detail_ids: tuple[str, ...] = ()
    detail_run_id: int | None = None
    """The run that discovered ``pending_detail_ids`` (it may already be closed)."""
    unit_total: int | None = None
    unit_resumed: bool = False
    """True once the unit was interrupted: a later COMPLETE covers only part
    of the unit, so it must not flip unseen listings inactive."""
    band_index: int | None = None
    """Deprecated: persisted only so pre-recursive-split rows still load. New
    logic treats ``pending_units`` as the queue, never this int."""

    @property
    def district(self) -> District:
        return DISTRICTS[self.district_index % len(DISTRICTS)]

    @property
    def district_id(self) -> str:
        return self.district.district_id

    @property
    def current_unit(self) -> ArchiveUnit:
        return ArchiveUnit(self.price_band, self.extra_filters)


def clamp_page_size(value: int) -> int:
    return min(max(int(value), 1), MAX_PAGE_SIZE)


def last_page(total: int, page_size: int) -> int:
    """Last page worth requesting for ``total`` hits (deep-paging capped)."""
    reachable = min(max(int(total), 0), LIST_TOTAL_CAP)
    size = clamp_page_size(page_size)
    return max(1, -(-reachable // size))


def parse_price_band(band: str) -> tuple[int, int]:
    """Parse ``"lo:hi"``; a high of ``-1`` means open-ended."""
    lo, hi = str(band).split(":", 1)
    return int(lo), int(hi)


def split_price_band(band: str) -> tuple[str, str] | None:
    """Halve one price band into two covering the same rent interval.

    * Open high (``lo:-1``): split at ``lo + max(500, lo)``; the second child
      stays open-ended.
    * Closed interval wider than 1 yuan: integer midpoint with ``lo < mid < hi``.
    * A 1-yuan band (``hi - lo <= 1``) cannot be split on price → ``None``;
      the caller then splits by delType instead.
    """
    lo, hi = parse_price_band(band)
    if hi == -1:
        cut = lo + max(500, lo)
        if cut <= lo:
            cut = lo + 500
        return f"{lo}:{cut}", f"{cut}:-1"
    if hi - lo <= 1:
        return None
    mid = lo + (hi - lo) // 2
    return f"{lo}:{mid}", f"{mid}:{hi}"


def needs_unit_split(cursor: ArchiveCursor, total: int, page: int) -> bool:
    """True only on a unit's first page when it overflows the paging cap.

    Applies to unsliced district probes *and* to already-sliced bands:
    splitting later pages would discard the rows already collected as the
    census of that unit.
    """
    del cursor
    return int(page) == 1 and int(total) > LIST_TOTAL_CAP


def can_split_unit(cursor: ArchiveCursor) -> bool:
    """Whether an over-cap unit still has an untried split dimension.

    Rent can always be halved until the band is 1 yuan wide; past that, the
    single remaining dimension is the 普租/托管 delType split, which may be
    used only once. A (1-yuan band, fixed delType) unit above 1000 has no
    further split and is swept to the reachable cap instead.
    """
    if cursor.price_band is None:
        return True
    if split_price_band(cursor.price_band) is not None:
        return True
    return not cursor.extra_filters


def _start_unit(
    cursor: ArchiveCursor, unit: ArchiveUnit, pending: tuple[ArchiveUnit, ...]
) -> ArchiveCursor:
    """Move to a freshly-queued unit of the same district."""
    return replace(
        cursor,
        price_band=unit.price_band,
        extra_filters=tuple(unit.extra_filters),
        pending_units=tuple(pending),
        page=1,
        run_id=None,
        seen_ids=(),
        pending_detail_ids=(),
        detail_run_id=None,
        unit_total=None,
        unit_resumed=False,
    )


def split_current_unit(cursor: ArchiveCursor) -> ArchiveCursor:
    """Replace an over-cap unit with smaller children and start the first.

    Precondition: :func:`can_split_unit` is true (an untried dimension
    remains).

    * Unsliced district probe: queue the initial :data:`PRICE_BANDS` grid.
    * Price band that still halves: two child bands, inheriting any extra
      filters, prepend the rest of the district's queue.
    * 1-yuan band: two units with the same band split by delType (2 普租 /
      5 托管), the only remaining list dimension that shrinks the census.
    """
    if cursor.price_band is None:
        units = tuple(ArchiveUnit(band, ()) for band in PRICE_BANDS)
        return _start_unit(cursor, units[0], units[1:])

    children = split_price_band(cursor.price_band)
    if children is not None:
        child_units = (
            ArchiveUnit(children[0], cursor.extra_filters),
            ArchiveUnit(children[1], cursor.extra_filters),
        )
        queue = child_units + cursor.pending_units
        return _start_unit(cursor, queue[0], queue[1:])

    split_units = (
        ArchiveUnit(cursor.price_band, (("delType", "2"),)),
        ArchiveUnit(cursor.price_band, (("delType", "5"),)),
    )
    queue = split_units + cursor.pending_units
    return _start_unit(cursor, queue[0], queue[1:])


def next_page(cursor: ArchiveCursor) -> ArchiveCursor:
    return replace(cursor, page=cursor.page + 1, run_id=cursor.run_id)


def is_skipped_district(district_id: str) -> bool:
    """True for far suburban / county districts the archive never crawls."""
    return district_id in SKIPPED_DISTRICT_IDS


def next_crawled_district_index(index: int) -> int:
    """Next catalog index still in crawl scope, wrapping once.

    Walks ``(index + 1) % len(DISTRICTS)`` past every id in
    :data:`SKIPPED_DISTRICT_IDS`; always terminates because 14 of the 24
    catalog districts remain crawled.
    """
    candidate = (int(index) + 1) % len(DISTRICTS)
    while is_skipped_district(DISTRICTS[candidate].district_id):
        candidate = (candidate + 1) % len(DISTRICTS)
    return candidate


def next_unit(cursor: ArchiveCursor) -> ArchiveCursor:
    """Advance past a finished unit.

    Drain the district's ``pending_units`` first; only then rotate to the
    next crawled district (skipped far-suburban ids are jumped over, wrapping
    to 0 after the last one) with an unsliced probe.
    """
    if cursor.pending_units:
        return _start_unit(cursor, cursor.pending_units[0], cursor.pending_units[1:])
    return ArchiveCursor(
        district_index=next_crawled_district_index(cursor.district_index)
    )


def build_filters(
    *,
    district_id: str,
    price_band: str | None,
    page: int,
    page_size: int,
    extra_filters: tuple[tuple[str, str], ...] = (),
) -> RentalListingFilters:
    """One list-page request: 全部房源 scope narrowed to a unit."""
    conditions: tuple[tuple[str, str | int | float], ...] = (
        ("districtId", district_id),
    )
    if price_band:
        conditions += (("price", price_band),)
    conditions += tuple(extra_filters)
    return RentalListingFilters(
        community_keyword=None,
        resblock_ids=(),
        listing_id=None,
        scope=LIST_SCOPE,
        monthly_rent_yuan=None,
        area_sqm=None,
        rooms=(),
        orientations=(),
        page=max(int(page), 1),
        page_size=clamp_page_size(page_size),
        condition_filters=conditions,
    )
