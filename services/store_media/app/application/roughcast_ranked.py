"""本地自用排名榜:从最新 COMPLETE 评分批次按 quality_status 拆组排序。

读 `roughcast_listing_scores` + `roughcast_listing_current`,**只读 SQLite**,
不打 CRM、不改评分器、不加历史回退。`/roughcast.html` 仍然走 CRM,这一页
是 shadow run 的本地自用入口,跟公开云发版是两件事(Phase 5 first cut)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.application.roughcast_review import (
    KE_LISTING_URL,
    _district_counts,
    _districts_for_row,
    _has_cover,
    _matches_districts,
    parse_selected_districts,
)
from app.infrastructure.featured_fetcher import original_image_url, public_image_url
from app.infrastructure.roughcast_repository import RoughcastRepository

SortKey = Literal["quality", "confidence", "rent_asc", "unit_rent_asc", "latest"]
GroupKey = Literal["scored", "nearby_estimate", "insufficient", "data_error"]
_VALID_SORTS: tuple[SortKey, ...] = (
    "quality", "confidence", "rent_asc", "unit_rent_asc", "latest",
)
_VALID_GROUPS: tuple[GroupKey, ...] = (
    "scored", "nearby_estimate", "insufficient", "data_error",
)
MIN_CONFIDENCE_MAX = 100
PAGE_SIZE_MAX = 50


@dataclass(frozen=True)
class RankedCard:
    listing_id: str
    city_rank: int | None
    community: str
    district: str | None
    bizcircle: str | None
    layout: str
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str | None
    floor: str | None
    unit_rent: float | None
    reference_unit_rent: float | None
    expected_unit_rent: float | None
    advantage: float | None
    quality_score: int | None
    quality_score_raw: float | None
    quality_status: str
    quality_tier: str | None
    confidence_score: int | None
    benchmark_mode: str | None
    peer_scope: str | None
    extreme_price: bool
    reason: str
    image: str | None
    ke_url: str
    view_count: int


@dataclass(frozen=True)
class RankedDistrictCount:
    name: str
    count: int


@dataclass(frozen=True)
class RankedFeed:
    score_run_id: int
    model_version: str | None
    delta_version: int | None
    delta_value: float | None
    scored_at: str | None
    listing_run_id: int | None
    sort_applied: SortKey
    group: GroupKey
    deals: bool
    min_confidence: int
    require_cover: bool
    selected_district: str | None
    selected_districts: tuple[str, ...]
    districts: tuple[RankedDistrictCount, ...]
    group_counts: dict[str, int]
    total: int
    page: int
    page_size: int
    has_more: bool
    items: tuple[RankedCard, ...]


def normalize_sort(value: str | None) -> SortKey:
    """未识别值回退到 `quality`,与 API 默认行为一致。"""
    if not value:
        return "quality"
    candidate = value.strip().lower()
    return candidate if candidate in _VALID_SORTS else "quality"   # type: ignore[return-value]


def normalize_group(value: str | None) -> GroupKey:
    if not value:
        return "scored"
    candidate = value.strip().lower()
    return candidate if candidate in _VALID_GROUPS else "scored"     # type: ignore[return-value]


def resolve_group(*, requested: GroupKey, deals: bool) -> GroupKey:
    """`deals=true` 强制走 scored,即便前端传了 nearby。

    高可信捡漏是 scored 子集,周边估算没有 confidence 可言,不会进
    这个集合——同 quality_score=95/90 在附近组也得被滤掉。
    """
    if deals:
        return "scored"
    return requested


def apply_group_filter(rows, group: GroupKey):
    return [row for row in rows if row["quality_status"] == group]


def apply_deals_filter(rows, deals: bool, *, group: GroupKey):
    """高可信捡漏:`quality_score >= 75 AND confidence_score >= 70` AND scored。

    周边估算根本进不到这一步——已经在外面强制 group=scored,这里再做一次
    兜底:即便 caller 误传 nearby 行进来,这里按 `quality_status == 'scored'`
    滤掉,避免 quality=95 / confidence=90 的附近组套把 deals 列表污染。

    质量门槛用整数 `quality_score`,跟前端 / brief / 设计一致,不用
    `quality_score_raw` 浮点。`raw=74.6` 经截断成 `score=75` 仍视为达标;
    反过来 `raw=74.6` 但整数 74 不达标的不进。
    """
    if not deals:
        return list(rows)
    if group != "scored":
        return []
    return [
        row for row in rows
        if row["quality_status"] == "scored"
        and (row["quality_score"] or 0) >= 75
        and (row["confidence_score"] or 0) >= 70
    ]


def apply_min_confidence(rows, threshold: int):
    if threshold <= 0:
        return list(rows)
    return [
        row for row in rows
        if (row["confidence_score"] or 0) >= threshold
    ]


def apply_require_cover(rows, require_cover: bool):
    if not require_cover:
        return list(rows)
    return [row for row in rows if _has_cover(row)]


def apply_districts(rows, catalog, selected):
    return [row for row in rows if _matches_districts(row, catalog, selected)]


def sort_rows(rows, sort: SortKey):
    """稳定排序。`NULLS LAST` 用 Python 端把 None 推到末尾。"""
    if sort == "quality":
        key = _quality_key
    elif sort == "confidence":
        key = _confidence_key
    elif sort == "rent_asc":
        key = _rent_key
    elif sort == "unit_rent_asc":
        key = _unit_rent_key
    elif sort == "latest":
        key = _latest_key
    else:
        key = _quality_key
    return sorted(rows, key=key)


def _quality_key(row):
    raw = row["quality_score_raw"]
    return (
        raw is None,
        -(raw or 0.0),
        -(row["confidence_score"] or 0),
        str(row["listing_id"]),
    )


def _confidence_key(row):
    return (
        row["confidence_score"] is None,
        -(row["confidence_score"] or 0),
        -(row["quality_score_raw"] or 0.0),
        str(row["listing_id"]),
    )


def _rent_key(row):
    value = row["monthly_rent_yuan"]
    return (
        value is None,
        value if value is not None else 0.0,
        str(row["listing_id"]),
    )


def _unit_rent_key(row):
    value = row["unit_rent"]
    return (
        value is None,
        value if value is not None else 0.0,
        str(row["listing_id"]),
    )


def _latest_key(row):
    """`create_time DESC` 回退 `last_seen_at DESC`,再 `listing_id ASC`;缺时间戳的排最后。

    ISO 8601 字符串按字典序即按时间序;要把比较翻成 DESC,只需把每个字符
    `chr(255 - ord(c))` 一下,字典序小的就是原始更晚的那一条。listing_id
    保持原序不上翻,避免 reverse=True 把 tie-breaker 也翻成 DESC。
    """
    stamp = row["create_time"] or row["last_seen_at"] or ""
    if not stamp:
        return (1, "", str(row["listing_id"]))
    flipped = "".join(chr(255 - ord(c)) for c in str(stamp))
    return (0, flipped, str(row["listing_id"]))


def paginate(rows, page: int, page_size: int):
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], len(rows), end < len(rows)


def _card(row, views, catalog) -> RankedCard:
    listing_id = str(row["listing_id"])
    raw_image = row["title_image_url"]
    image = original_image_url(raw_image) or public_image_url(raw_image)
    layout = row["layout"] or (
        f"{row['rooms'] or 0}室{row['halls'] or 0}厅{row['baths'] or 0}卫"
    )
    bizcircle = str(row["bizcircle"] or "").strip() or None
    district_names = _districts_for_row(row, catalog=catalog)
    return RankedCard(
        listing_id=listing_id,
        city_rank=row["city_rank"],
        community=str(row["community_name"] or ""),
        district="/".join(district_names) if district_names else None,
        bizcircle=bizcircle,
        layout=str(layout),
        area_sqm=row["area_sqm"],
        monthly_rent_yuan=row["monthly_rent_yuan"],
        orientation=str(row["orientation"]).strip() if row["orientation"] else None,
        floor=str(row["floor_desc"]).strip() if row["floor_desc"] else None,
        unit_rent=row["unit_rent"],
        reference_unit_rent=row["reference_unit_rent"],
        expected_unit_rent=row["expected_unit_rent"],
        advantage=row["advantage"],
        quality_score=row["quality_score"],
        quality_score_raw=row["quality_score_raw"],
        quality_status=str(row["quality_status"] or ""),
        quality_tier=row["quality_tier"],
        confidence_score=row["confidence_score"],
        benchmark_mode=row["benchmark_mode"],
        peer_scope=row["peer_scope"],
        extreme_price=bool(row["extreme_price"]),
        reason=str(row["reason"] or ""),
        image=image,
        ke_url=KE_LISTING_URL.format(listing_id=listing_id),
        view_count=int(views.get(listing_id, 0) or 0),
    )


def build_ranked_feed(
    repository: RoughcastRepository,
    *,
    catalog=None,
    page: int = 1,
    page_size: int = 30,
    sort: SortKey | str = "quality",
    min_confidence: int = 0,
    districts_filter=None,
    district: str | None = None,
    deals: bool = False,
    group: GroupKey | str = "scored",
    require_cover: bool = False,
) -> RankedFeed | None:
    """Phase 5 first cut:本地排名榜。

    `return None` 表示「没有 COMPLETE score run」,路由把它转 404。其它
    情况(空组、空筛选)正常返回 RankedFeed(total=0, items=())。
    """
    run = repository.latest_score_run()
    if run is None:
        return None

    resolved_sort: SortKey = normalize_sort(str(sort) if sort else None)
    requested_group: GroupKey = normalize_group(str(group) if group else None)
    resolved_group: GroupKey = resolve_group(requested=requested_group, deals=deals)
    resolved_page = max(int(page or 1), 1)
    resolved_size = max(1, min(int(page_size or 30), PAGE_SIZE_MAX))
    resolved_min_conf = max(0, min(int(min_confidence or 0), MIN_CONFIDENCE_MAX))

    from app.infrastructure.district_catalog import empty_district_catalog
    resolved_catalog = catalog or empty_district_catalog()
    selected = parse_selected_districts(district, *(districts_filter or ()))

    rows = repository.list_score_review_rows(int(run["id"]))

    # 1) group 切:这一步必须先做,因为 `apply_deals_filter` 短路要靠
    #    caller 承诺 group=scored;下面 `deals=true` + group!=scored 直接
    #    返回空,正好对上需求。
    in_group = apply_group_filter(rows, resolved_group)
    in_group = apply_require_cover(in_group, require_cover)

    # 2) chips 计数在行政区筛选 / deals / min_confidence 之前——
    #    跟 `/score-review.html` 一致:用户选了「成华」也得看得到其它区,
    #    才能继续切换。`group_counts` 仍按行政区 + cover 之后算,这是
    #    既有的顶部计数语义,不动。
    pre_district = list(in_group)
    in_group = apply_districts(in_group, resolved_catalog, selected)

    # 3) `group_counts` 在 deals/min_confidence 之前计数,与 review 现有约定一致
    group_counts: dict[str, int] = {}
    for key in _VALID_GROUPS:
        per_group = apply_group_filter(rows, key)
        per_group = apply_require_cover(per_group, require_cover)
        per_group = apply_districts(per_group, resolved_catalog, selected)
        group_counts[key] = len(per_group)

    # 4) 最后一层:deals / min_confidence 才落到 working set
    working = apply_deals_filter(in_group, deals, group=resolved_group)
    working = apply_min_confidence(working, resolved_min_conf)

    districts = tuple(
        RankedDistrictCount(name=item.name, count=item.count)
        for item in _district_counts(pre_district, resolved_catalog)
    )
    sorted_rows = sort_rows(working, resolved_sort)
    page_rows, total, has_more = paginate(sorted_rows, resolved_page, resolved_size)

    views = repository.review_view_counts()
    items = tuple(_card(row, views, resolved_catalog) for row in page_rows)
    selected_str = "、".join(selected) if selected else None
    return RankedFeed(
        score_run_id=int(run["id"]),
        model_version=run["model_version"] if "model_version" in run.keys() else None,
        delta_version=run["delta_version"] if "delta_version" in run.keys() else None,
        delta_value=run["delta_value"] if "delta_value" in run.keys() else None,
        scored_at=run["finished_at"] if "finished_at" in run.keys() else None,
        listing_run_id=run["listing_run_id"] if "listing_run_id" in run.keys() else None,
        sort_applied=resolved_sort,
        group=resolved_group,
        deals=bool(deals),
        min_confidence=resolved_min_conf,
        require_cover=bool(require_cover),
        selected_district=selected_str,
        selected_districts=selected,
        districts=districts,
        group_counts=group_counts,
        total=total,
        page=resolved_page,
        page_size=resolved_size,
        has_more=has_more,
        items=items,
    )
