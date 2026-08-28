"""内部核分清单:从最新 COMPLETE 评分批次抽出几组给网页看。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from app.infrastructure.district_catalog import (
    UNKNOWN_DISTRICT,
    DistrictCatalog,
    empty_district_catalog,
)
from app.infrastructure.featured_fetcher import original_image_url, public_image_url
from app.infrastructure.roughcast_repository import RoughcastRepository

# 核分页「打开贝壳」走 CRM 工作台详情，不走需要 C 端登录的 cd.ke.com。
KE_LISTING_URL = "https://lease-pz.link.lianjia.com/rent/house/detail/{listing_id}"
TOP_N = 12
BOTTOM_N = 12
EXTREME_N = 12
MID_N = 12
CORE_DISTRICTS = ("锦江", "青羊", "金牛", "武侯", "成华")


@dataclass(frozen=True)
class ReviewCard:
    listing_id: str
    city_rank: int | None
    community: str
    district: str | None
    bizcircle: str | None
    layout: str
    area_sqm: float | None
    monthly_rent_yuan: float | None
    unit_rent: float | None
    reference_unit_rent: float | None
    expected_unit_rent: float | None
    quality_score: int | None
    confidence_score: int | None
    benchmark_mode: str | None
    extreme_price: bool
    reason: str
    image: str | None
    ke_url: str
    group_hint: str
    view_count: int


@dataclass(frozen=True)
class DistrictCount:
    name: str
    count: int


@dataclass(frozen=True)
class ReviewFeed:
    score_run_id: int
    delta_value: float | None
    scored_count: int
    filtered_count: int
    selected_district: str | None
    selected_districts: tuple[str, ...]
    require_cover: bool
    districts: tuple[DistrictCount, ...]
    groups: dict[str, tuple[ReviewCard, ...]]


def parse_selected_districts(*raw: str | None) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not item:
            continue
        for part in str(item).split(","):
            name = part.strip()
            if not name or name in seen or len(name) > 32:
                continue
            seen.add(name)
            names.append(name)
    return tuple(names)


def build_review_feed(
    repository: RoughcastRepository,
    *,
    catalog: DistrictCatalog | None = None,
    district: str | None = None,
    districts_filter: tuple[str, ...] | list[str] | None = None,
    require_cover: bool = False,
) -> ReviewFeed | None:
    run = repository.latest_score_run()
    if run is None:
        return None
    resolved_catalog = catalog or empty_district_catalog()
    selected = parse_selected_districts(
        district,
        *(districts_filter or ()),
    )
    rows = repository.list_score_review_rows(int(run["id"]))
    scored = [row for row in rows if row["quality_status"] == "scored"]
    if require_cover:
        scored = [row for row in scored if _has_cover(row)]
    districts = _district_counts(scored, resolved_catalog)
    visible = [
        row for row in scored
        if _matches_districts(row, resolved_catalog, selected)
    ]
    views = repository.review_view_counts()
    scored_by_rank = sorted(
        visible, key=lambda row: (row["city_rank"] is None, row["city_rank"] or 10**9)
    )
    bottom = list(reversed(scored_by_rank[-BOTTOM_N:])) if scored_by_rank else []
    extreme = sorted(
        [row for row in scored_by_rank if row["extreme_price"]],
        key=lambda row: abs(row["advantage"] or 0),
        reverse=True,
    )[:EXTREME_N]
    mid = [
        row for row in scored_by_rank
        if not row["extreme_price"]
        and (row["confidence_score"] or 0) >= 70
        and 70 <= (row["quality_score"] or 0) <= 88
        and row["benchmark_mode"] in {"S_ONLY", "S2_BLEND"}
    ][:MID_N]
    groups = {
        "top": tuple(_card(row, "最高分", resolved_catalog, views) for row in scored_by_rank[:TOP_N]),
        "bottom": tuple(_card(row, "最低分", resolved_catalog, views) for row in bottom),
        "extreme": tuple(_card(row, "待核极端", resolved_catalog, views) for row in extreme),
        "mid": tuple(_card(row, "高可信对照", resolved_catalog, views) for row in mid),
    }
    return ReviewFeed(
        score_run_id=int(run["id"]),
        delta_value=run["delta_value"],
        scored_count=int(run["scored_count"] or 0),
        filtered_count=len(visible),
        selected_district="、".join(selected) if selected else None,
        selected_districts=selected,
        require_cover=require_cover,
        districts=districts,
        groups=groups,
    )


def _has_cover(row) -> bool:
    raw = row["title_image_url"] if "title_image_url" in row.keys() else None
    if not isinstance(raw, str):
        return False
    return bool(original_image_url(raw) or public_image_url(raw))


def _districts_for_row(row, catalog: DistrictCatalog) -> tuple[str, ...]:
    raw = row["districts_json"] if "districts_json" in row.keys() else None
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            names = tuple(str(item).strip() for item in parsed if str(item).strip())
            if names:
                return names
    persisted = row["persisted_district"] if "persisted_district" in row.keys() else None
    if persisted:
        return (str(persisted),)
    return catalog.districts_for(row["bizcircle"])


def _matches_districts(
    row, catalog: DistrictCatalog, wanted: tuple[str, ...]
) -> bool:
    if not wanted:
        return True
    names = _districts_for_row(row, catalog)
    if UNKNOWN_DISTRICT in wanted and not names:
        return True
    return any(name in wanted for name in names)


def _district_counts(rows, catalog: DistrictCatalog) -> tuple[DistrictCount, ...]:
    counts: Counter[str] = Counter()
    unknown = 0
    for row in rows:
        names = _districts_for_row(row, catalog)
        if names:
            counts.update(names)
        else:
            unknown += 1
    ordered = [
        DistrictCount(name=name, count=counts[name])
        for name, _count in counts.most_common()
        if counts[name] > 0
    ]
    if unknown:
        ordered.append(DistrictCount(name=UNKNOWN_DISTRICT, count=unknown))
    return tuple(ordered)


def _card(row, hint: str, catalog: DistrictCatalog, views: dict[str, int]) -> ReviewCard:
    layout = row["layout"] or (
        f"{row['rooms'] or 0}室{row['halls'] or 0}厅{row['baths'] or 0}卫"
    )
    listing_id = str(row["listing_id"])
    raw_image = row["title_image_url"]
    image = original_image_url(raw_image) or public_image_url(raw_image)
    bizcircle = str(row["bizcircle"] or "").strip() or None
    district_names = _districts_for_row(row, catalog)
    return ReviewCard(
        listing_id=listing_id,
        city_rank=row["city_rank"],
        community=str(row["community_name"] or ""),
        district="/".join(district_names) if district_names else None,
        bizcircle=bizcircle,
        layout=str(layout),
        area_sqm=row["area_sqm"],
        monthly_rent_yuan=row["monthly_rent_yuan"],
        unit_rent=row["unit_rent"],
        reference_unit_rent=row["reference_unit_rent"],
        expected_unit_rent=row["expected_unit_rent"],
        quality_score=row["quality_score"],
        confidence_score=row["confidence_score"],
        benchmark_mode=row["benchmark_mode"],
        extreme_price=bool(row["extreme_price"]),
        reason=str(row["reason"] or ""),
        image=image,
        ke_url=KE_LISTING_URL.format(listing_id=listing_id),
        group_hint=hint,
        view_count=int(views.get(listing_id, 0) or 0),
    )
