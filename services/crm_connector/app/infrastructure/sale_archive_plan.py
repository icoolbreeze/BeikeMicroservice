"""Static crawl plan for the 买卖 archive: districts, bands, cursor math.

The archive walks the 全部房源 list the way an employee does, restricted to
the same Chengdu districts the rental archive crawls (jumping the same ten
far-suburban districts), page by page. A unit whose first page reports more
hits than the upstream deep-paging cap is split into smaller units — first by
总价, then by 面积, then by 室数 — so every listing stays reachable.

Unlike rental's list, the 买卖 ``searchQueryNew`` endpoint ignores ``pageSize``
and always returns 30 rows; :data:`PAGE_SIZE` is therefore a fixed constant,
not a setting, and no pageSize is sent on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.domain.models import SaleListingFilters

# Upstream ignores pageSize and serves 30 rows per request.
PAGE_SIZE = 30
LIST_SCOPE = "all"

# The rental 房源列表 upstream stops serving rows past 1000 hits (deep-paging
# cap measured 2026-08-21, run/probe_deep_paging.py). The sale searchQueryNew
# endpoint is assumed to share that same 1000-row cap; a unit above it is
# unreachable in one sweep and must be sliced.
LIST_TOTAL_CAP = 1000

# Extra-filter keys (carried on the cursor, mapped back into SaleListingFilters).
AREA = "area"
ROOM = "room"

# Sort stays on the page default: 新上房源优先.
DEFAULT_SORT = "period1_desc_createtime_desc"


@dataclass(frozen=True)
class District:
    """One Chengdu administrative district as the CRM identifies it."""

    name: str
    district_id: str


# Same catalog, order and ids as the rental plan (sale disId, verified from
# run/sale_filters_response.json). A live cursor stores its position as an
# index into this tuple, so the first five entries keep their original order
# and the catalog is append-only.
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

# Far suburban / county districts are out of sale localization scope: the
# crawl jumps over them (kept as the sale plan's own copy, separate from the
# rental plan, so the two archives stay independent).
SKIPPED_DISTRICT_IDS: frozenset[str] = frozenset({
    "510113", "510132", "510181", "23009119", "510121",
    "510184", "510182", "510129", "510183", "510131",
})

# Comma-separated 总价 bands in 万元, the shape the upstream ``price`` token
# takes. This is the *initial* grid (skipping 不限); fat bands are halved at
# runtime. A high of -1 means open-ended.
PRICE_BANDS: tuple[str, ...] = (
    "0,50", "50,70", "70,90", "90,120", "120,150",
    "150,200", "200,300", "300,500", "500,-1",
)

# Comma-separated 面积 bands in ㎡, queued only when a 1-万 price band still
# overflows (the price band is kept).
AREA_BANDS: tuple[str, ...] = (
    "0,50", "50,70", "70,90", "90,110", "110,130",
    "130,150", "150,200", "200,-1",
)

# Room bands queued only when a (1-万 band, 1-㎡ area) slice still overflows.
# The last band is 5,-1 (5 室及以上) rather than 5,5 so 5-室 listings are not
# double-counted.
ROOM_BANDS: tuple[str, ...] = ("1,1", "2,2", "3,3", "4,4", "5,-1")


@dataclass(frozen=True)
class ArchiveUnit:
    """One reachable slice of a district: a 总价 band plus optional area/room
    list conditions."""

    price_band: str | None
    extra_filters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ArchiveCursor:
    """Where the district sweep stands, persisted between ticks.

    A *unit* is one district plus one price band and optionally area/room
    conditions; only a unit that finished every page may deactivate listings
    it did not see. ``price_band is None`` with no extra filters is the
    unsliced first-page probe of a district.
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
    """True once the unit was interrupted (or only partly reachable): a later
    COMPLETE covers only part of the unit, so it must not flip unseen listings
    inactive."""

    @property
    def district(self) -> District:
        return DISTRICTS[self.district_index % len(DISTRICTS)]

    @property
    def district_id(self) -> str:
        return self.district.district_id

    @property
    def current_unit(self) -> ArchiveUnit:
        return ArchiveUnit(self.price_band, self.extra_filters)


def last_page(total: int) -> int:
    """Last page worth requesting for ``total`` hits (deep-paging capped)."""
    reachable = min(max(int(total), 0), LIST_TOTAL_CAP)
    return max(1, -(-reachable // PAGE_SIZE))


def parse_band(token: str) -> tuple[int, int]:
    """Parse a comma ``"lo,hi"`` band; a high of ``-1`` means open-ended."""
    lo, hi = str(token).split(",", 1)
    return int(lo), int(hi)


# Kept under the rental-parallel name; price and area bands share the comma
# shape, so this is :func:`parse_band`.
parse_price_band = parse_band


def split_price_band(band: str) -> tuple[str, str] | None:
    """Halve one 总价 band into two covering the same interval.

    * Open high (``lo,-1``): split at ``lo + max(100, lo)``; the second child
      stays open-ended (``500,-1`` -> ``500,1000`` and ``1000,-1``).
    * Closed interval wider than 1 万: integer midpoint with ``lo < mid < hi``.
    * A 1-万 band (``hi - lo <= 1``) cannot be split on price -> ``None``; the
      caller then splits by area instead.
    """
    lo, hi = parse_band(band)
    if hi == -1:
        cut = lo + max(100, lo)
        return f"{lo},{cut}", f"{cut},-1"
    if hi - lo <= 1:
        return None
    mid = lo + (hi - lo) // 2
    return f"{lo},{mid}", f"{mid},{hi}"


def split_area_band(band: str) -> tuple[str, str] | None:
    """Halve one 面积 band into two covering the same interval.

    * Open high (``lo,-1``): split at ``lo + max(50, lo)`` (``200,-1`` ->
      ``200,400`` and ``400,-1``).
    * Closed interval wider than 1 ㎡: integer midpoint.
    * A 1-㎡ band cannot be split -> ``None``; the caller then splits by room.
    """
    lo, hi = parse_band(band)
    if hi == -1:
        cut = lo + max(50, lo)
        return f"{lo},{cut}", f"{cut},-1"
    if hi - lo <= 1:
        return None
    mid = lo + (hi - lo) // 2
    return f"{lo},{mid}", f"{mid},{hi}"


def needs_unit_split(cursor: ArchiveCursor, total: int, page: int) -> bool:
    """True only on a unit's first page when it overflows the paging cap.

    Splitting later pages would discard the rows already collected as the
    census of that unit.
    """
    del cursor
    return int(page) == 1 and int(total) > LIST_TOTAL_CAP


def _filter_value(extra_filters: tuple[tuple[str, str], ...], key: str) -> str | None:
    for candidate, value in extra_filters:
        if candidate == key:
            return value
    return None


def can_split_unit(cursor: ArchiveCursor) -> bool:
    """Whether an over-cap unit still has an untried split dimension.

    总价 can always be halved until the band is 1 万 wide; past that the grid
    moves to 面积 (halvable to 1 ㎡) and then to 室数. A (1-万 band, 1-㎡ area,
    single room) unit above 1000 has no further split and is swept to the
    reachable cap instead.
    """
    if cursor.price_band is None:
        return True
    if split_price_band(cursor.price_band) is not None:
        return True
    area = _filter_value(cursor.extra_filters, AREA)
    if area is None:
        return True
    if split_area_band(area) is not None:
        return True
    return _filter_value(cursor.extra_filters, ROOM) is None


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
    remains). The split ladder, in order:

    * Unsliced district probe: queue the initial :data:`PRICE_BANDS` grid.
    * Price band that still halves: two child bands inheriting area/room.
    * 1-万 band with no area filter: queue :data:`AREA_BANDS`, keeping price.
    * Area band that still halves: two children inheriting price (and room).
    * 1-㎡ area with no room filter: queue :data:`ROOM_BANDS`, keeping price
      and area.
    """
    if cursor.price_band is None:
        units = tuple(ArchiveUnit(band, ()) for band in PRICE_BANDS)
        return _start_unit(cursor, units[0], units[1:])

    price_children = split_price_band(cursor.price_band)
    if price_children is not None:
        child_units = (
            ArchiveUnit(price_children[0], cursor.extra_filters),
            ArchiveUnit(price_children[1], cursor.extra_filters),
        )
        queue = child_units + cursor.pending_units
        return _start_unit(cursor, queue[0], queue[1:])

    area = _filter_value(cursor.extra_filters, AREA)
    if area is None:
        units = tuple(
            ArchiveUnit(cursor.price_band, ((AREA, band),)) for band in AREA_BANDS
        )
        queue = units + cursor.pending_units
        return _start_unit(cursor, queue[0], queue[1:])

    area_children = split_area_band(area)
    if area_children is not None:
        base_extra = tuple(
            (key, value) for key, value in cursor.extra_filters if key != AREA
        )
        child_units = (
            ArchiveUnit(cursor.price_band, base_extra + ((AREA, area_children[0]),)),
            ArchiveUnit(cursor.price_band, base_extra + ((AREA, area_children[1]),)),
        )
        queue = child_units + cursor.pending_units
        return _start_unit(cursor, queue[0], queue[1:])

    # 1-㎡ area, no room filter (guaranteed by can_split_unit): keep the area
    # row and append the room condition.
    area_extra = tuple(
        (key, value) for key, value in cursor.extra_filters if key != ROOM
    )
    units = tuple(
        ArchiveUnit(cursor.price_band, area_extra + ((ROOM, band),))
        for band in ROOM_BANDS
    )
    queue = units + cursor.pending_units
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
    to 0 after 天府新区南区) with an unsliced probe.
    """
    if cursor.pending_units:
        return _start_unit(cursor, cursor.pending_units[0], cursor.pending_units[1:])
    return ArchiveCursor(
        district_index=next_crawled_district_index(cursor.district_index)
    )


def unit_signature(
    price_band: str | None, extra_filters: tuple[tuple[str, str], ...]
) -> str | None:
    """Full stored signature of a unit for the ``price_band`` column.

    * Unsliced district: ``None``.
    * Price only: ``"90,120"``.
    * Price + area: ``"90,120|area=70,90"``.
    * Price + area + room: ``"90,120|area=70,90|room=5,-1"``.

    Deactivation is scoped to this signature so one finished slice cannot
    retire listings from a sibling slice.
    """
    parts: list[str] = []
    if price_band:
        parts.append(price_band)
    for key, value in extra_filters:
        parts.append(f"{key}={value}")
    if not parts:
        return None
    return "|".join(parts)


def build_filters(
    *,
    district_id: str,
    price_band: str | None,
    page: int,
    extra_filters: tuple[tuple[str, str], ...] = (),
) -> SaleListingFilters:
    """One list-page request: 全部房源 scope narrowed to a crawled unit."""
    price_wan: tuple[float, float | None] | None = None
    if price_band:
        lo, hi = parse_band(price_band)
        price_wan = (lo, None) if hi == -1 else (lo, hi)

    area_sqm: tuple[float, float | None] | None = None
    area_token = _filter_value(extra_filters, AREA)
    if area_token is not None:
        lo, hi = parse_band(area_token)
        area_sqm = (lo, None) if hi == -1 else (lo, hi)

    room_range: tuple[int, int] | None = None
    room_token = _filter_value(extra_filters, ROOM)
    if room_token is not None:
        room_range = parse_band(room_token)

    return SaleListingFilters(
        scope=LIST_SCOPE,
        community_ids=(),
        district_id=district_id,
        listing_id=None,
        price_wan=price_wan,
        area_sqm=area_sqm,
        rooms=(),
        floors=(),
        orientations=(),
        house_layouts=(),
        tags=(),
        select=(),
        house_age=None,
        visitable_times=None,
        payment_mode=None,
        building_type=None,
        sort=DEFAULT_SORT,
        page=max(int(page), 1),
        room_range=room_range,
    )
