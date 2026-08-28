"""离线复算三个覆盖率（§六 第 3 期 / 第九章）。

读本地 `listing_current`(P) 与指针指向的 `community_reference_snapshot`(R),
零上游请求。`classify` / Resolver 的口径由 `ClassifyConfig` 切换,用来对照
第 2 期修正前/后的覆盖率,定稿 2.2 阶梯。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence

from app.domain.roughcast import REFERENCE_FITMENTS
from app.domain.roughcast_benchmark import (
    CORRECTED,
    ClassifyConfig,
    ListingView,
    classify_comparable,
    is_data_error,
    is_likely_parking,
    resolve_community_benchmark,
)
from app.infrastructure.roughcast_repository import RoughcastRepository


@dataclass(frozen=True)
class CoverageReport:
    config_name: str
    targets: int
    eligible: int
    data_error: int
    rooms_zero: int
    with_s: int
    with_sa: int
    valid: int
    modes: Mapping[str, int]
    rooms_s_rate: Mapping[int, tuple[int, int]]
    fitment_unit_rent: Mapping[str, tuple[int, float | None]]
    communities_with_r: int
    communities_total: int

    @property
    def s_grade_coverage(self) -> float:
        return _ratio(self.with_s, self.eligible)

    @property
    def strong_comparable_coverage(self) -> float:
        return _ratio(self.with_sa, self.eligible)

    @property
    def valid_community_benchmark_coverage(self) -> float:
        return _ratio(self.valid, self.eligible)


def load_targets(repository: RoughcastRepository) -> list[ListingView]:
    rows = repository.list_active_listings()
    return [_listing_from_current(row) for row in rows]


def load_references_by_community(
    repository: RoughcastRepository,
) -> dict[str, list[ListingView]]:
    grouped: dict[str, list[ListingView]] = defaultdict(list)
    for row in repository.list_pointer_references():
        listing = _listing_from_snapshot(row)
        if listing.community_id:
            grouped[str(listing.community_id)].append(listing)
    return dict(grouped)


def compute_coverage(
    targets: Sequence[ListingView],
    references: Mapping[str, Sequence[ListingView]],
    *,
    config: ClassifyConfig = CORRECTED,
    config_name: str = "corrected",
) -> CoverageReport:
    eligible_ids: list[str] = []
    data_error = 0
    rooms_zero = 0
    with_s = 0
    with_sa = 0
    valid = 0
    modes: Counter[str] = Counter()
    rooms_hits: dict[int, list[bool]] = defaultdict(list)
    for target in targets:
        if not (target.rooms and target.rooms > 0):
            rooms_zero += 1
            continue
        if is_likely_parking(target) or is_data_error(target):
            data_error += 1
            continue
        eligible_ids.append(target.listing_id)
        cands = list(references.get(target.community_id or "", ()))
        result = resolve_community_benchmark(target, cands, config)
        modes[result.benchmark_mode] += 1
        has_s = False
        has_a = False
        for cand in cands:
            grade = classify_comparable(target, cand, config)
            if grade == "S":
                has_s = True
            if grade in {"S", "A"}:
                has_a = True
            if has_s and has_a:
                break
        if has_s:
            with_s += 1
        if has_a:
            with_sa += 1
        if result.is_valid:
            valid += 1
        rooms_hits[int(target.rooms or 0)].append(has_s)

    rooms_s_rate = {
        rooms: (sum(1 for hit in hits if hit), len(hits))
        for rooms, hits in sorted(rooms_hits.items())
    }
    return CoverageReport(
        config_name=config_name,
        targets=len(targets),
        eligible=len(eligible_ids),
        data_error=data_error,
        rooms_zero=rooms_zero,
        with_s=with_s,
        with_sa=with_sa,
        valid=valid,
        modes=dict(modes),
        rooms_s_rate=rooms_s_rate,
        fitment_unit_rent=_fitment_unit_rents(references),
        communities_with_r=sum(
            1 for rows in references.values()
            if any(row.fitment_status in REFERENCE_FITMENTS for row in rows)
        ),
        communities_total=len(references),
    )


def render_coverage(report: CoverageReport) -> str:
    lines = [
        f"清水房覆盖率 · {report.config_name}",
        f"  在架 P {report.targets} / 参与分母 {report.eligible}"
        f"（剔除 data_error {report.data_error}、0 室 {report.rooms_zero}）",
        f"  s_grade_coverage                {_pct(report.s_grade_coverage)}"
        f"  {report.with_s}/{report.eligible}   告警线 30%",
        f"  strong_comparable_coverage      {_pct(report.strong_comparable_coverage)}"
        f"  {report.with_sa}/{report.eligible}   告警线 50%",
        f"  valid_community_benchmark_coverage {_pct(report.valid_community_benchmark_coverage)}"
        f"  {report.valid}/{report.eligible}   告警线 60%",
        "  benchmark_mode:",
    ]
    for mode, count in sorted(report.modes.items(), key=lambda item: -item[1]):
        lines.append(f"    {mode:<16} {count:5d}  {_pct(count / report.eligible if report.eligible else 0)}")
    lines.append("  S 命中率按室数:")
    for rooms, (hits, total) in report.rooms_s_rate.items():
        lines.append(f"    {rooms}室  {hits}/{total}  {_pct(hits / total if total else 0)}")
    lines.append(
        f"  有 R 的小区 {report.communities_with_r}/{report.communities_total}"
    )
    lines.append("  参考单价(元/㎡) 001 vs 003:")
    for fitment, (count, med) in sorted(report.fitment_unit_rent.items()):
        med_text = "—" if med is None else f"{med:.2f}"
        lines.append(f"    {fitment}  n={count}  median={med_text}")
    return "\n".join(lines)


def _fitment_unit_rents(
    references: Mapping[str, Sequence[ListingView]],
) -> dict[str, tuple[int, float | None]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for rows in references.values():
        for row in rows:
            status = row.fitment_status or "(empty)"
            unit = row.resolved_unit_rent
            if unit is None:
                continue
            buckets[status].append(unit)
    return {
        key: (len(values), median(values) if values else None)
        for key, values in sorted(buckets.items())
    }


def _listing_from_current(row) -> ListingView:
    return ListingView(
        listing_id=str(row["listing_id"]),
        community_id=row["community_id"],
        rent_mode=row["rent_mode"],
        rooms=row["rooms"],
        halls=row["halls"],
        baths=row["baths"],
        area_sqm=row["area_sqm"],
        monthly_rent_yuan=row["monthly_rent_yuan"],
        fitment_status=row["fitment_status"],
    )


def _listing_from_snapshot(row) -> ListingView:
    return ListingView(
        listing_id=str(row["listing_id"]),
        community_id=row["community_id"],
        rent_mode=row["rent_mode"],
        rooms=row["rooms"],
        halls=row["halls"],
        baths=row["baths"],
        area_sqm=row["area_sqm"],
        monthly_rent_yuan=row["monthly_rent_yuan"],
        unit_rent=row["unit_rent"],
        fitment_status=row["fitment_status"],
    )


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"
