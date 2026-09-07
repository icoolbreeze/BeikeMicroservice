"""Comparable 分级与 Resolver（`docs/roughcast-quality-ranking.md` §2.2 / §2.3）。

第 3 期落地,带上第 2 期探针的四条修正:

1. S/A 面积窗口用相对偏差(默认 r≤0.05 / r≤0.10),与 sim_area 的 σ=0.20 同口径
2. S/A 不再要求室厅卫三元组全等:室相等,厅/卫各允许差 1
3. halls==0 且 baths==0 视为户型未知,不参与全等
4. n_S==1 且 secondary 不足时落到 A/B/C/D 阶梯,不直接 INVALID

本模块是纯函数,零 I/O。覆盖率复算与将来的评分器共用这一份实现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

from app.domain.roughcast import REFERENCE_FITMENTS, unit_rent_of

Grade = Literal["S", "A", "B", "C", "D"]
GRADES: tuple[Grade, ...] = ("S", "A", "B", "C", "D")

BenchmarkMode = Literal[
    "S_ONLY", "S2_BLEND", "S2_ONLY_WEAK", "S1_BLEND",
    "A_ONLY", "B_ONLY", "C_ONLY", "D_ONLY", "NEARBY", "INVALID",
]

GRADE_SCORE: dict[Grade, int] = {"S": 100, "A": 85, "B": 65, "C": 45, "D": 25}
LADDER_MODES: dict[Grade, BenchmarkMode] = {
    "A": "A_ONLY", "B": "B_ONLY", "C": "C_ONLY", "D": "D_ONLY",
}

# 户型-面积一致性哨兵。1室 260㎡ 这类行不得进参考池(测试 X)。
# 键是室数,值是允许的 [min, max] ㎡。未列出的室数不拦。
DEFAULT_AREA_LAYOUT_BOUNDS: dict[int, tuple[float, float]] = {
    1: (12.0, 100.0),
    2: (25.0, 160.0),
    3: (40.0, 220.0),
    4: (60.0, 320.0),
    5: (80.0, 400.0),
}

UNIT_RENT_MIN = 1.0
UNIT_RENT_MAX = 500.0
MIN_AREA_SQM = 5.0


@dataclass(frozen=True)
class ClassifyConfig:
    """分级与 Resolver 的可切换口径。默认即第 2 期四条修正后的定稿候选。"""

    s_area_relative: float = 0.05
    a_area_relative: float = 0.10
    s_area_abs: float = 5.0
    a_area_abs: float = 10.0
    use_relative_area: bool = True
    require_exact_layout_for_sa: bool = False
    hall_bath_slack: int = 1
    treat_zero_hb_as_unknown: bool = True
    ns1_fallthrough: bool = True
    b_sim_area_min: float = 0.6
    c_sim_area_min: float = 0.5
    sim_area_sigma: float = 0.20
    secondary_min: int = 2
    ladder_min: int = 3
    area_layout_bounds: Mapping[int, tuple[float, float]] = field(
        default_factory=lambda: DEFAULT_AREA_LAYOUT_BOUNDS
    )


CORRECTED = ClassifyConfig()
LEGACY = ClassifyConfig(
    use_relative_area=False,
    require_exact_layout_for_sa=True,
    hall_bath_slack=0,
    treat_zero_hb_as_unknown=False,
    ns1_fallthrough=False,
)


@dataclass(frozen=True)
class ListingView:
    listing_id: str
    community_id: str | None = None
    rent_mode: str | None = None
    rooms: int | None = None
    halls: int | None = None
    baths: int | None = None
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    unit_rent: float | None = None
    fitment_status: str | None = None
    age_days: int | None = None          # 当前态 0/None;历史样本的天数
    bizcircle: str | None = None
    community_name: str | None = None
    last_seen_run_id: int | None = None
    captured_at: str | None = None
    run_id: int | None = None

    @property
    def resolved_unit_rent(self) -> float | None:
        if self.unit_rent is not None:
            return self.unit_rent
        return unit_rent_of(self.area_sqm, self.monthly_rent_yuan)


@dataclass(frozen=True)
class GradedSample:
    listing_id: str
    unit_rent: float
    grade: Grade
    weight: float


@dataclass(frozen=True)
class BenchmarkResult:
    is_valid: bool
    benchmark: float | None
    benchmark_mode: BenchmarkMode
    n_s: int
    n_a: int
    n_b: int
    n_c: int
    n_d: int
    n_eff: float
    structure_cap: int
    peer_scope: str
    pool_listing_ids: tuple[str, ...] = ()
    pool_samples: tuple[GradedSample, ...] = ()

    @property
    def grade_vector(self) -> tuple[int, int, int, int, int]:
        return (self.n_s, self.n_a, self.n_b, self.n_c, self.n_d)


def is_likely_parking(listing: ListingView) -> bool:
    """1室0厅0卫（业内常写成 1-0-0-0）在租赁库里大量是车位/储藏间,不是住房。

    不进被排序集 P、不进参考集 R。2/3 室的 0H0B 仍按户型未知处理,不在这条里。
    """
    return listing.rooms == 1 and listing.halls == 0 and listing.baths == 0


def is_data_error(listing: ListingView) -> bool:
    """§2.7:面积/租金缺失或单价越界,不给分。"""
    if is_likely_parking(listing):
        return True
    if listing.area_sqm is None or listing.area_sqm <= MIN_AREA_SQM:
        return True
    if listing.monthly_rent_yuan is None or listing.monthly_rent_yuan <= 0:
        return True
    unit = listing.resolved_unit_rent
    if unit is None or unit < UNIT_RENT_MIN or unit > UNIT_RENT_MAX:
        return True
    return False


def is_layout_area_outlier(listing: ListingView, config: ClassifyConfig = CORRECTED) -> bool:
    """户型-面积哨兵。越界的参考行不得进 R(测试 X)。"""
    if listing.rooms is None or listing.area_sqm is None:
        return False
    bounds = config.area_layout_bounds.get(int(listing.rooms))
    if bounds is None:
        return False
    low, high = bounds
    return listing.area_sqm < low or listing.area_sqm > high


def sim_area(target_area: float, candidate_area: float, *, sigma: float = 0.20) -> float:
    if target_area <= 0:
        return 0.0
    ratio = abs(target_area - candidate_area) / target_area
    return math.exp(-((ratio / sigma) ** 2) / 2.0)


def _zero_hb(listing: ListingView) -> bool:
    return listing.halls == 0 and listing.baths == 0


def _effective_halls(listing: ListingView, config: ClassifyConfig) -> int | None:
    if listing.halls is None:
        return None
    if config.treat_zero_hb_as_unknown and _zero_hb(listing):
        return None
    return listing.halls


def _effective_baths(listing: ListingView, config: ClassifyConfig) -> int | None:
    if listing.baths is None:
        return None
    if config.treat_zero_hb_as_unknown and _zero_hb(listing):
        return None
    return listing.baths


def _rooms_known(listing: ListingView) -> bool:
    """0 室不当精确键(测试 W):既可能是单间也可能是缺失。"""
    return listing.rooms is not None and listing.rooms > 0


def sim_layout(target: ListingView, candidate: ListingView,
               config: ClassifyConfig = CORRECTED) -> float:
    if not _rooms_known(target) or not _rooms_known(candidate):
        return 0.0
    room_diff = abs(target.rooms - candidate.rooms)  # type: ignore[operator]
    if room_diff >= 2:
        return 0.20
    if room_diff == 1:
        return 0.50
    th, ch = _effective_halls(target, config), _effective_halls(candidate, config)
    tb, cb = _effective_baths(target, config), _effective_baths(candidate, config)
    if th is None or ch is None:
        return 0.80
    if th != ch:
        return 0.80
    if tb is None or cb is None or tb != cb:
        return 0.90
    return 1.00


def _layout_ok_for_sa(target: ListingView, candidate: ListingView,
                      config: ClassifyConfig) -> bool:
    if not _rooms_known(target) or not _rooms_known(candidate):
        return False
    if target.rooms != candidate.rooms:
        return False
    th, ch = _effective_halls(target, config), _effective_halls(candidate, config)
    tb, cb = _effective_baths(target, config), _effective_baths(candidate, config)
    if config.require_exact_layout_for_sa:
        if th is None or ch is None or tb is None or cb is None:
            return False
        return th == ch and tb == cb
    slack = config.hall_bath_slack
    if th is not None and ch is not None and abs(th - ch) > slack:
        return False
    if tb is not None and cb is not None and abs(tb - cb) > slack:
        return False
    return True


def _area_rel(target: ListingView, candidate: ListingView) -> float | None:
    if target.area_sqm is None or candidate.area_sqm is None or target.area_sqm <= 0:
        return None
    return abs(target.area_sqm - candidate.area_sqm) / target.area_sqm


def _area_abs(target: ListingView, candidate: ListingView) -> float | None:
    if target.area_sqm is None or candidate.area_sqm is None:
        return None
    return abs(target.area_sqm - candidate.area_sqm)


def freshness_weight(age_days: int | None) -> float:
    """当前态 1.00;历史 ≤30/60/90 天 0.85/0.70/0.55。超过 90 天丢弃。"""
    if age_days is None or age_days <= 0:
        return 1.00
    if age_days <= 30:
        return 0.85
    if age_days <= 60:
        return 0.70
    if age_days <= 90:
        return 0.55
    return 0.0


def classify_comparable(
    target: ListingView, candidate: ListingView,
    config: ClassifyConfig = CORRECTED,
    *,
    require_same_community: bool = True,
) -> Grade | None:
    """返回单个最高等级,或不互比时 None。命中即停,S∩A∩B∩C∩D = ∅。"""
    if is_likely_parking(target) or is_likely_parking(candidate):
        return None
    if candidate.fitment_status not in REFERENCE_FITMENTS:
        return None
    if target.rent_mode != candidate.rent_mode:
        return None
    if not _rooms_known(target) or not _rooms_known(candidate):
        return None
    if is_layout_area_outlier(candidate, config):
        return None
    if candidate.resolved_unit_rent is None:
        return None
    if require_same_community and target.community_id and candidate.community_id and (
        target.community_id != candidate.community_id
    ):
        return None

    sa_layout = _layout_ok_for_sa(target, candidate, config)
    if config.use_relative_area:
        rel = _area_rel(target, candidate)
        if sa_layout and rel is not None:
            if rel <= config.s_area_relative:
                return "S"
            if rel <= config.a_area_relative:
                return "A"
    else:
        abs_diff = _area_abs(target, candidate)
        if sa_layout and abs_diff is not None:
            if abs_diff <= config.s_area_abs:
                return "S"
            if abs_diff <= config.a_area_abs:
                return "A"

    area_sim = 0.0
    if target.area_sqm and candidate.area_sqm and target.area_sqm > 0:
        area_sim = sim_area(
            target.area_sqm, candidate.area_sqm, sigma=config.sim_area_sigma,
        )
    if target.rooms == candidate.rooms and area_sim >= config.b_sim_area_min:
        return "B"
    if abs(target.rooms - candidate.rooms) == 1 and area_sim >= config.c_sim_area_min:
        return "C"
    return "D"


def classify_all(
    target: ListingView, candidates: Sequence[ListingView],
    config: ClassifyConfig = CORRECTED,
    *,
    require_same_community: bool = True,
) -> list[GradedSample]:
    samples: list[GradedSample] = []
    for candidate in candidates:
        grade = classify_comparable(
            target, candidate, config, require_same_community=require_same_community,
        )
        if grade is None:
            continue
        unit = candidate.resolved_unit_rent
        if unit is None:
            continue
        fresh = freshness_weight(candidate.age_days)
        if fresh <= 0:
            continue
        layout = sim_layout(target, candidate, config)
        area_sim = 0.0
        if target.area_sqm and candidate.area_sqm and target.area_sqm > 0:
            area_sim = sim_area(
                target.area_sqm, candidate.area_sqm, sigma=config.sim_area_sigma,
            )
        weight = layout * area_sim * fresh
        samples.append(GradedSample(
            listing_id=candidate.listing_id, unit_rent=unit,
            grade=grade, weight=weight if weight > 0 else 1e-9,
        ))
    return samples


def weighted_median(pairs: Sequence[tuple[float, float]]) -> float:
    """按值排序,权重累加过半处的样本值。空池未定义。"""
    cleaned = [(value, weight) for value, weight in pairs if weight > 0]
    if not cleaned:
        raise ValueError("weighted_median 收到空池")
    cleaned.sort(key=lambda item: item[0])
    total = sum(weight for _, weight in cleaned)
    half = total / 2.0
    accumulated = 0.0
    for value, weight in cleaned:
        accumulated += weight
        if accumulated >= half:
            return value
    return cleaned[-1][0]


def _unique_by_listing(samples: Sequence[GradedSample]) -> list[GradedSample]:
    by_id: dict[str, GradedSample] = {}
    for sample in samples:
        by_id[sample.listing_id] = sample
    return list(by_id.values())


def _count_vector(samples: Sequence[GradedSample]) -> dict[Grade, list[GradedSample]]:
    buckets: dict[Grade, list[GradedSample]] = {grade: [] for grade in GRADES}
    for sample in _unique_by_listing(samples):
        buckets[sample.grade].append(sample)
    return buckets


def _wmedian_of(samples: Sequence[GradedSample]) -> float:
    return weighted_median([(sample.unit_rent, sample.weight) for sample in samples])


def _neff(samples: Sequence[GradedSample]) -> float:
    return sum(sample.weight for sample in samples)


def resolve_community_benchmark(
    target: ListingView, candidates: Sequence[ListingView],
    config: ClassifyConfig = CORRECTED,
    *,
    require_same_community: bool = True,
    peer_scope: str = "community",
) -> BenchmarkResult:
    """本小区当前参考集 → BenchmarkResult。INVALID 时调用方走回退链。"""
    if is_data_error(target) or is_likely_parking(target) or not _rooms_known(target):
        return _invalid(n_s=0, n_a=0, n_b=0, n_c=0, n_d=0)
    samples = classify_all(
        target, candidates, config, require_same_community=require_same_community,
    )
    result = resolve_pool(samples, config)
    if peer_scope != "community":
        return BenchmarkResult(**{**result.__dict__, "peer_scope": peer_scope})
    return result


def resolve_pool(
    samples: Sequence[GradedSample],
    config: ClassifyConfig = CORRECTED,
) -> BenchmarkResult:
    buckets = _count_vector(samples)
    n_s, n_a, n_b, n_c, n_d = (len(buckets[g]) for g in GRADES)
    secondary = buckets["A"] + buckets["B"]
    secondary_ok = len(secondary) >= config.secondary_min

    if n_s >= 3:
        return _valid("S_ONLY", buckets["S"], 100, n_s, n_a, n_b, n_c, n_d)
    if n_s == 2 and secondary_ok:
        benchmark = 0.80 * _wmedian_of(buckets["S"]) + 0.20 * _wmedian_of(secondary)
        used = buckets["S"] + secondary
        return BenchmarkResult(
            is_valid=True, benchmark=benchmark, benchmark_mode="S2_BLEND",
            n_s=n_s, n_a=n_a, n_b=n_b, n_c=n_c, n_d=n_d,
            n_eff=_neff(used), structure_cap=100, peer_scope="community",
            pool_listing_ids=tuple(sample.listing_id for sample in used),
            pool_samples=tuple(used),
        )
    if n_s == 2:
        return _valid("S2_ONLY_WEAK", buckets["S"], 70, n_s, n_a, n_b, n_c, n_d)
    if n_s == 1 and secondary_ok:
        unique_s = buckets["S"][0].unit_rent
        benchmark = 0.40 * unique_s + 0.60 * _wmedian_of(secondary)
        used = buckets["S"] + secondary
        return BenchmarkResult(
            is_valid=True, benchmark=benchmark, benchmark_mode="S1_BLEND",
            n_s=n_s, n_a=n_a, n_b=n_b, n_c=n_c, n_d=n_d,
            n_eff=_neff(used), structure_cap=60, peer_scope="community",
            pool_listing_ids=tuple(sample.listing_id for sample in used),
            pool_samples=tuple(used),
        )
    if n_s == 1 and not config.ns1_fallthrough:
        return _invalid(n_s, n_a, n_b, n_c, n_d)

    for grade in ("A", "B", "C", "D"):
        bucket = buckets[grade]
        if len(bucket) >= config.ladder_min:
            return _valid(
                LADDER_MODES[grade], bucket, GRADE_SCORE[grade],
                n_s, n_a, n_b, n_c, n_d,
            )
    return _invalid(n_s, n_a, n_b, n_c, n_d)


def _valid(
    mode: BenchmarkMode, pool: Sequence[GradedSample], cap: int,
    n_s: int, n_a: int, n_b: int, n_c: int, n_d: int,
) -> BenchmarkResult:
    return BenchmarkResult(
        is_valid=True,
        benchmark=_wmedian_of(pool),
        benchmark_mode=mode,
        n_s=n_s, n_a=n_a, n_b=n_b, n_c=n_c, n_d=n_d,
        n_eff=_neff(pool),
        structure_cap=cap,
        peer_scope="community",
        pool_listing_ids=tuple(sample.listing_id for sample in pool),
        pool_samples=tuple(pool),
    )


def _invalid(n_s: int, n_a: int, n_b: int, n_c: int, n_d: int) -> BenchmarkResult:
    return BenchmarkResult(
        is_valid=False, benchmark=None, benchmark_mode="INVALID",
        n_s=n_s, n_a=n_a, n_b=n_b, n_c=n_c, n_d=n_d,
        n_eff=0.0, structure_cap=0, peer_scope="community",
    )
