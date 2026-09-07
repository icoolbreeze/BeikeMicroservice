"""优质指数、可信度与 δ 标定（`docs/roughcast-quality-ranking.md` §2.5 / §2.6 / §2.7）。

评分器与 δ 标定必须调用同一个 Resolver（测试 4）。本模块是纯函数。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence

from app.domain.roughcast_benchmark import (
    GRADE_SCORE,
    BenchmarkResult,
    GradedSample,
    ListingView,
    is_data_error,
    is_likely_parking,
)

MODEL_VERSION = "v2.1.0"
K_SCALE = 0.50                       # provisional,全量后可重估
DELTA_VERSION = 1
# 探针 δ≈0.42–0.49,全量后实测会更接近 0.4。原 [0.55,0.95] 会把真值判失败,
# 把全市分数抬高一截。护栏按下沿放到 0.30,失败才回退。
DELTA_GUARD = (0.30, 0.95)
DELTA_FALLBACK = 0.45
EXTREME_ADVANTAGE = 0.7
SCORE_MIN = 5.0
SCORE_MAX = 95.0

PRIMARY_FITMENT = "003"
SECONDARY_FITMENT = "001"

DELTA_ELIGIBLE_MODES = frozenset({
    "S_ONLY", "S2_BLEND", "S2_ONLY_WEAK", "A_ONLY", "B_ONLY",
})
STRONG_NEARBY_MODES = frozenset({"S_ONLY", "S2_BLEND", "A_ONLY"})
NEARBY_SPREAD_MAX = 0.25
NEARBY_MIN_NEIGHBORS = 3

MODE_GRADE: dict[str, str] = {
    "S_ONLY": "S", "S2_BLEND": "S", "S2_ONLY_WEAK": "S", "S1_BLEND": "S",
    "A_ONLY": "A", "B_ONLY": "B", "C_ONLY": "C", "D_ONLY": "D",
    "NEARBY": "N",
}
SOURCE_CAP = {
    "community": 100,
    "community_history_30": 80,
    "community_history_60": 70,
    "community_history_90": 60,
    "nearby": 45,
}


@dataclass(frozen=True)
class DeltaEstimate:
    value: float
    version: int
    used_fallback: bool
    community_count: int
    listing_count: int
    raw_value: float | None
    note: str


@dataclass(frozen=True)
class ScoreResult:
    listing_id: str
    unit_rent: float | None
    reference_unit_rent: float | None
    expected_unit_rent: float | None
    advantage: float | None
    quality_score_raw: float | None
    quality_score: int | None
    quality_status: str
    quality_tier: str | None
    confidence_score: int | None
    city_rank: int | None
    peer_scope: str | None
    comparable_grade: str | None
    benchmark_mode: str | None
    effective_sample_count: float | None
    reference_age_days: int | None
    reference_community_count: int | None
    reference_spread: float | None
    extreme_price: int
    reason: str
    benchmark_pool_json: str
    listing_run_id: int | None
    reference_run_id: int | None
    r_fitment: str | None = None


def quality_from_advantage(advantage: float, *, k: float = K_SCALE) -> tuple[float, int]:
    """固定映射。与其他房源无关。round 用 half-up,避开银行家舍入。"""
    raw = 50.0 + 45.0 * math.tanh(advantage / k)
    raw = max(SCORE_MIN, min(SCORE_MAX, raw))
    return raw, int(math.floor(raw + 0.5))


def nearby_tier(quality_score_raw: float) -> str:
    if quality_score_raw >= 70:
        return "疑似划算"
    if quality_score_raw >= 40:
        return "一般"
    return "疑似偏贵"


def confidence_score(
    result: BenchmarkResult, *,
    source_cap: int,
    freshness: float,
) -> int:
    """唯一截断点:min(raw, source_cap, structure_cap)。"""
    grade = _grade_dimension(result)
    sample = 100.0 * (1.0 - math.exp(-result.n_eff / 3.0))
    similarity = _similarity_dimension(result.pool_samples)
    raw = 0.40 * grade + 0.25 * sample + 0.20 * freshness + 0.15 * similarity
    clipped = min(raw, float(source_cap), float(result.structure_cap))
    return int(math.floor(clipped + 0.5))


def _grade_dimension(result: BenchmarkResult) -> float:
    if result.benchmark_mode == "NEARBY":
        return 15.0
    letter = MODE_GRADE.get(result.benchmark_mode)
    if letter and letter in GRADE_SCORE:
        return float(GRADE_SCORE[letter])  # type: ignore[index]
    return 25.0


def _similarity_dimension(samples: Sequence[GradedSample]) -> float:
    if not samples:
        return 0.0
    # GradedSample.weight 在当前态 = sim_layout × sim_area × w_fresh
    return 100.0 * (sum(sample.weight for sample in samples) / len(samples))


def calibrate_delta(
    rows: Sequence[tuple[str, float, float, str]],
    *,
    fallback: float = DELTA_FALLBACK,
    guard: tuple[float, float] = DELTA_GUARD,
) -> DeltaEstimate:
    """小区等权:每个小区先取 u/benchmark 的中位数,再全市取中位数。"""
    by_community: dict[str, list[float]] = {}
    used = 0
    for community_id, unit_rent, benchmark, mode in rows:
        if mode not in DELTA_ELIGIBLE_MODES:
            continue
        if benchmark <= 0 or unit_rent <= 0:
            continue
        by_community.setdefault(community_id, []).append(unit_rent / benchmark)
        used += 1
    if not by_community:
        return DeltaEstimate(
            value=fallback, version=DELTA_VERSION, used_fallback=True,
            community_count=0, listing_count=0, raw_value=None,
            note="无合格样本,回退配置值",
        )
    community_medians = [median(values) for values in by_community.values()]
    raw = float(median(community_medians))
    low, high = guard
    if raw < low or raw > high:
        return DeltaEstimate(
            value=fallback, version=DELTA_VERSION, used_fallback=True,
            community_count=len(by_community), listing_count=used, raw_value=raw,
            note=f"标定 {raw:.4f} 越出 [{low},{high}],回退 {fallback}",
        )
    return DeltaEstimate(
        value=raw, version=DELTA_VERSION, used_fallback=False,
        community_count=len(by_community), listing_count=used, raw_value=raw,
        note=f"小区等权 {len(by_community)} 个小区 / {used} 套",
    )


def apply_score(
    target: ListingView,
    resolved: BenchmarkResult,
    *,
    delta: float,
    k: float = K_SCALE,
    source_cap: int = 100,
    freshness: float = 100.0,
    reference_age_days: int = 0,
    reference_run_id: int | None = None,
    r_fitment: str | None = None,
    nearby_k: int | None = None,
    nearby_spread: float | None = None,
    nearby_pool: Sequence[Mapping[str, object]] | None = None,
) -> ScoreResult:
    unit = target.resolved_unit_rent
    if is_likely_parking(target):
        return ScoreResult(
            listing_id=target.listing_id, unit_rent=unit,
            reference_unit_rent=None, expected_unit_rent=None, advantage=None,
            quality_score_raw=None, quality_score=None, quality_status="data_error",
            quality_tier=None, confidence_score=None, city_rank=None,
            peer_scope=None, comparable_grade=None, benchmark_mode="INVALID",
            effective_sample_count=None, reference_age_days=None,
            reference_community_count=None, reference_spread=None, extreme_price=0,
            reason="疑似车位/非住房（1室0厅0卫），不评分",
            benchmark_pool_json="[]",
            listing_run_id=target.last_seen_run_id,
            reference_run_id=reference_run_id, r_fitment=r_fitment,
        )
    if not resolved.is_valid or resolved.benchmark is None or unit is None or unit <= 0:
        status = "data_error" if is_data_error(target) else "insufficient"
        return ScoreResult(
            listing_id=target.listing_id, unit_rent=unit,
            reference_unit_rent=None, expected_unit_rent=None, advantage=None,
            quality_score_raw=None, quality_score=None, quality_status=status,
            quality_tier=None, confidence_score=None, city_rank=None,
            peer_scope=resolved.peer_scope if resolved.benchmark_mode != "INVALID" else None,
            comparable_grade=None, benchmark_mode=resolved.benchmark_mode,
            effective_sample_count=resolved.n_eff or None,
            reference_age_days=None, reference_community_count=None,
            reference_spread=None, extreme_price=0,
            reason="无可用基准" if status == "insufficient" else "房源数据异常,不评分",
            benchmark_pool_json="[]",
            listing_run_id=target.last_seen_run_id,
            reference_run_id=reference_run_id, r_fitment=r_fitment,
        )

    reference = resolved.benchmark
    expected = reference * delta
    advantage = math.log(expected / unit)
    raw, display = quality_from_advantage(advantage, k=k)
    extreme = 1 if abs(advantage) > EXTREME_ADVANTAGE else 0
    conf = confidence_score(resolved, source_cap=source_cap, freshness=freshness)
    if resolved.peer_scope == "nearby":
        status = "nearby_estimate"
        tier = nearby_tier(raw)
        rankable = False
    else:
        status = "scored"
        tier = None
        rankable = True
    grade = MODE_GRADE.get(resolved.benchmark_mode)
    reason = (
        f"装修参考单价 {reference:.2f} 元/㎡ × δ {delta:.3f} "
        f"= 合理清水价 {expected:.2f} 元/㎡；实际 {unit:.2f} 元/㎡；"
        f"{resolved.benchmark_mode} / {resolved.peer_scope}"
        + (f"；R={r_fitment}" if r_fitment else "")
        + (f"；周边 k={nearby_k} spread={nearby_spread:.3f}" if nearby_spread is not None else "")
    )
    pool_payload: list[dict[str, object]] = [
        {
            "listing_id": sample.listing_id,
            "unit_rent": sample.unit_rent,
            "grade": sample.grade,
            "weight": sample.weight,
        }
        for sample in resolved.pool_samples
    ]
    if nearby_pool:
        pool_payload = list(nearby_pool)
    return ScoreResult(
        listing_id=target.listing_id,
        unit_rent=unit,
        reference_unit_rent=reference,
        expected_unit_rent=expected,
        advantage=advantage,
        quality_score_raw=raw,
        quality_score=display,
        quality_status=status,
        quality_tier=tier,
        confidence_score=conf,
        city_rank=None,  # 调用方按 scored 集合再填
        peer_scope=resolved.peer_scope,
        comparable_grade=grade,
        benchmark_mode=resolved.benchmark_mode,
        effective_sample_count=resolved.n_eff,
        reference_age_days=reference_age_days,
        reference_community_count=nearby_k,
        reference_spread=nearby_spread,
        extreme_price=extreme,
        reason=reason,
        benchmark_pool_json=json.dumps(pool_payload, ensure_ascii=False),
        listing_run_id=target.last_seen_run_id,
        reference_run_id=reference_run_id,
        r_fitment=r_fitment,
    )


def assign_city_ranks(scores: Sequence[ScoreResult]) -> list[ScoreResult]:
    """只在 quality_status='scored' 里排:raw DESC, confidence DESC, listing_id ASC。"""
    scored = [row for row in scores if row.quality_status == "scored"]
    scored.sort(
        key=lambda row: (
            -(row.quality_score_raw or 0.0),
            -(row.confidence_score or 0),
            row.listing_id,
        )
    )
    ranks = {row.listing_id: index for index, row in enumerate(scored, start=1)}
    out: list[ScoreResult] = []
    for row in scores:
        rank = ranks.get(row.listing_id) if row.quality_status == "scored" else None
        if rank == row.city_rank:
            out.append(row)
            continue
        out.append(ScoreResult(**{**row.__dict__, "city_rank": rank}))
    return out
