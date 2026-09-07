"""评分公式、δ 标定与 Shadow 编排（§2.5 / §2.6 / 第八章）。"""

from __future__ import annotations

import math

import pytest

from app.domain.roughcast_benchmark import (
    BenchmarkResult,
    GradedSample,
    ListingView,
    resolve_community_benchmark,
)
from app.domain.roughcast_score import (
    K_SCALE,
    apply_score,
    assign_city_ranks,
    calibrate_delta,
    confidence_score,
    quality_from_advantage,
)
from app.domain.roughcast import unit_rent_of
from tests.unit.test_roughcast_benchmark import TARGET, lv, _samples_for_vector


def test_fixed_mapping_matches_documented_table() -> None:
    """测试 11:advantage → quality_score 与 2.6 表逐行一致,且与其他房无关。"""
    table = [
        (-0.262, 28),
        (-0.182, 34),
        (-0.140, 38),
        (-0.095, 42),
        (-0.049, 46),
        (0.0, 50),
        (0.051, 55),
        (0.105, 59),
        (0.163, 64),
        (0.223, 69),
        (0.357, 78),
        (0.511, 85),
        (0.693, 90),
    ]
    for advantage, expected in table:
        raw, display = quality_from_advantage(advantage)
        assert display == expected
        assert 5 <= raw <= 95


def test_quality_does_not_depend_on_other_listings() -> None:
    raw_a, _ = quality_from_advantage(0.105)
    raw_b, _ = quality_from_advantage(0.105)
    assert raw_a == raw_b


def test_expected_unit_rent_is_reference_times_delta() -> None:
    """测试 I。"""
    refs = _samples_for_vector(n_s=3, unit=100.0)
    resolved = resolve_community_benchmark(TARGET, refs)
    score = apply_score(TARGET, resolved, delta=0.5)
    assert score.expected_unit_rent == pytest.approx(
        score.reference_unit_rent * 0.5, rel=1e-6,
    )
    assert score.advantage == pytest.approx(
        math.log(score.expected_unit_rent / score.unit_rent), rel=1e-6,
    )
    assert "装修参考单价" in score.reason
    assert "S_ONLY" in score.reason


def test_delta_is_community_equal_weighted() -> None:
    """测试 3:20 套的小区与 20 个各 1 套的小区等权。"""
    dense = [("BIG", 40.0, 80.0, "S_ONLY") for _ in range(20)]
    sparse = [(f"C{i}", 50.0, 100.0, "S_ONLY") for i in range(20)]
    # 大小区比值 0.5,小小区 0.5 → 全市中位数 0.5,大小区不能把 δ 拉偏。
    estimate = calibrate_delta(dense + sparse)
    assert estimate.value == pytest.approx(0.5)
    assert estimate.community_count == 21
    assert estimate.used_fallback is False


def test_delta_ignores_weak_modes() -> None:
    rows = [("C1", 10.0, 100.0, "D_ONLY") for _ in range(10)]
    estimate = calibrate_delta(rows, fallback=0.45)
    assert estimate.used_fallback is True
    assert estimate.value == 0.45


def test_density_invariance_same_price() -> None:
    """测试 1:同小区 2 套 vs 20 套同价,quality_score 精确相等。"""
    refs = _samples_for_vector(n_s=3, unit=80.0)
    two = [lv("T0", monthly_rent_yuan=4000.0), lv("T1", monthly_rent_yuan=4000.0)]
    twenty = [lv(f"T{i}", monthly_rent_yuan=4000.0) for i in range(20)]

    def scores(targets):
        resolved = resolve_community_benchmark(targets[0], refs)
        # 同价目标共享同一份 benchmark(约束 3)
        return [apply_score(t, resolved, delta=0.5).quality_score for t in targets]

    two_scores = scores(two)
    twenty_scores = scores(twenty)
    assert two_scores[0] == two_scores[1]
    assert twenty_scores[0] == twenty_scores[-1]
    assert two_scores[0] == twenty_scores[0]


def test_twenty_d_cannot_outconfidence_three_s() -> None:
    """测试 5:数量买不到可信度。"""
    s_refs = _samples_for_vector(n_s=3, unit=100.0)
    d_refs = _samples_for_vector(n_d=20, unit=100.0)
    s_res = resolve_community_benchmark(TARGET, s_refs)
    d_res = resolve_community_benchmark(TARGET, d_refs)
    s_conf = confidence_score(s_res, source_cap=100, freshness=100)
    d_conf = confidence_score(d_res, source_cap=100, freshness=100)
    assert s_res.benchmark_mode == "S_ONLY"
    assert d_res.benchmark_mode == "D_ONLY"
    assert d_conf < s_conf
    assert d_conf <= 25


def test_rank_uses_raw_not_rounded() -> None:
    """测试 H:82.49 排在 82.01 前面,即使展示分都是 82。"""
    from app.domain.roughcast_score import ScoreResult
    a = ScoreResult(
        listing_id="A", unit_rent=1, reference_unit_rent=1, expected_unit_rent=1,
        advantage=0, quality_score_raw=82.49, quality_score=82, quality_status="scored",
        quality_tier=None, confidence_score=50, city_rank=None, peer_scope="community",
        comparable_grade="S", benchmark_mode="S_ONLY", effective_sample_count=3,
        reference_age_days=0, reference_community_count=None, reference_spread=None,
        extreme_price=0, reason="", benchmark_pool_json="[]", listing_run_id=1,
        reference_run_id=1,
    )
    b = ScoreResult(**{**a.__dict__, "listing_id": "B", "quality_score_raw": 82.01})
    ranked = assign_city_ranks([b, a])
    by_id = {row.listing_id: row.city_rank for row in ranked}
    assert by_id["A"] == 1
    assert by_id["B"] == 2


def test_nearby_is_not_in_city_rank() -> None:
    from app.domain.roughcast_score import ScoreResult
    nearby = ScoreResult(
        listing_id="N", unit_rent=1, reference_unit_rent=40, expected_unit_rent=20,
        advantage=0.2, quality_score_raw=92.0, quality_score=92,
        quality_status="nearby_estimate", quality_tier="疑似划算",
        confidence_score=45, city_rank=None, peer_scope="nearby",
        comparable_grade="N", benchmark_mode="NEARBY", effective_sample_count=3,
        reference_age_days=0, reference_community_count=3, reference_spread=0.1,
        extreme_price=0, reason="", benchmark_pool_json="[]", listing_run_id=1,
        reference_run_id=1,
    )
    community = ScoreResult(**{**nearby.__dict__, "listing_id": "C",
                               "quality_score_raw": 88.0, "quality_score": 88,
                               "quality_status": "scored", "peer_scope": "community",
                               "quality_tier": None, "benchmark_mode": "S_ONLY"})
    ranked = assign_city_ranks([nearby, community])
    by_id = {row.listing_id: row for row in ranked}
    assert by_id["N"].city_rank is None
    assert by_id["C"].city_rank == 1


def test_extreme_price_is_still_scored() -> None:
    refs = _samples_for_vector(n_s=3, unit=100.0)
    cheap = lv("X", monthly_rent_yuan=128.0 * 20.0)  # u=20 vs ref=100, δ=0.5 → expected 50
    resolved = resolve_community_benchmark(cheap, refs)
    score = apply_score(cheap, resolved, delta=0.5)
    # ln(50/20)=0.916 > 0.7
    assert score.extreme_price == 1
    assert score.quality_status == "scored"
    assert score.quality_score is not None


def test_one_zero_zero_parking_is_data_error() -> None:
    parking = lv("P", rooms=1, halls=0, baths=0, area_sqm=12.0, monthly_rent_yuan=400.0)
    refs = _samples_for_vector(n_s=3)
    resolved = resolve_community_benchmark(parking, refs)
    score = apply_score(parking, resolved, delta=0.5)
    assert score.quality_status == "data_error"
    assert score.quality_score is None
    assert "车位" in score.reason


def test_rooms_zero_is_insufficient() -> None:
    target = lv("Z", rooms=0, halls=0, baths=0, area_sqm=29.0, monthly_rent_yuan=400.0)
    refs = _samples_for_vector(n_s=3)
    resolved = resolve_community_benchmark(target, refs)
    score = apply_score(target, resolved, delta=0.5)
    assert score.quality_status == "insufficient"
    assert score.quality_score is None
