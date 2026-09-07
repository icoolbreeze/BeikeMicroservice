"""classify / Resolver（§2.2 / §2.3 与第 2 期四条修正）。"""

from __future__ import annotations

import pytest

from app.domain.roughcast_benchmark import (
    CORRECTED,
    LEGACY,
    ListingView,
    classify_all,
    classify_comparable,
    resolve_community_benchmark,
    resolve_pool,
    sim_area,
    weighted_median,
)
from app.domain.roughcast import unit_rent_of


def lv(listing_id: str, **overrides) -> ListingView:
    data = {
        "community_id": "C1",
        "rent_mode": "整租",
        "rooms": 3,
        "halls": 2,
        "baths": 2,
        "area_sqm": 128.0,
        "monthly_rent_yuan": 8000.0,
        "fitment_status": "003",
    }
    data.update(overrides)
    if "unit_rent" not in overrides:
        data["unit_rent"] = unit_rent_of(data.get("area_sqm"), data.get("monthly_rent_yuan"))
    return ListingView(listing_id=listing_id, **data)


TARGET = lv("T")


# ------------------------------------------------------------------ classify


def test_sim_area_documented_points() -> None:
    assert sim_area(100, 100) == 1.0
    assert abs(sim_area(100, 95) - 0.9692) < 0.002
    assert abs(sim_area(100, 80) - 0.6065) < 0.01


def test_grade_is_exclusive_s_not_also_a(tmp_path=None) -> None:
    """测试 A:面积差 2㎡ 的全等户型只能是 S。"""
    candidate = lv("R1", area_sqm=130.0, monthly_rent_yuan=8100.0)
    assert classify_comparable(TARGET, candidate) == "S"
    grades = [classify_comparable(TARGET, candidate)]
    assert grades == ["S"]


def test_relative_area_window_saves_large_layouts() -> None:
    """修正 1:4 室 184㎡ ±6㎡ 在绝对 5㎡ 下过不了 S,相对 5% 能过。"""
    target = lv("T4", rooms=4, area_sqm=184.0, monthly_rent_yuan=9000.0)
    candidate = lv("R4", rooms=4, area_sqm=190.0, monthly_rent_yuan=12000.0)
    assert classify_comparable(target, candidate, CORRECTED) == "S"
    assert classify_comparable(target, candidate, LEGACY) != "S"


def test_hall_or_bath_diff_one_still_s() -> None:
    """修正 2:厅或卫差 1 仍可入 S/A。"""
    candidate = lv("R1", baths=3, area_sqm=128.0, monthly_rent_yuan=8500.0)
    assert classify_comparable(TARGET, candidate, CORRECTED) == "S"
    assert classify_comparable(TARGET, candidate, LEGACY) != "S"
    assert classify_comparable(TARGET, candidate, LEGACY) == "B"


def test_zero_hb_is_unknown_not_exact_key() -> None:
    """修正 3:2室0厅0卫 对 2室1厅1卫 按户型未知,仍可比。1室0厅0卫走车位规则,见下。"""
    target = lv("T0", rooms=2, halls=0, baths=0, area_sqm=78.0, monthly_rent_yuan=3500.0)
    candidate = lv("R0", rooms=2, halls=1, baths=1, area_sqm=78.0, monthly_rent_yuan=4800.0)
    assert classify_comparable(target, candidate, CORRECTED) == "S"
    assert classify_comparable(target, candidate, LEGACY) != "S"


def test_one_zero_zero_is_treated_as_parking() -> None:
    """1室0厅0卫（1-0-0-0）疑似车位:不进 P、不进 R。"""
    parking = lv("P", rooms=1, halls=0, baths=0, area_sqm=12.0, monthly_rent_yuan=400.0)
    studio = lv("S", rooms=1, halls=1, baths=1, area_sqm=32.0, monthly_rent_yuan=2000.0,
                fitment_status="003")
    assert classify_comparable(parking, studio) is None
    assert classify_comparable(studio, parking) is None
    assert resolve_community_benchmark(parking, [studio]).is_valid is False


def test_rent_modes_are_isolated() -> None:
    """测试 9:合租不进整租参考池。"""
    candidate = lv("H1", rent_mode="合租", area_sqm=128.0)
    assert classify_comparable(TARGET, candidate) is None


def test_unknown_fitment_is_not_reference() -> None:
    """测试 U:空装修两边都不进 R。"""
    assert classify_comparable(TARGET, lv("U1", fitment_status=None)) is None
    assert classify_comparable(TARGET, lv("U2", fitment_status="")) is None
    assert classify_comparable(TARGET, lv("P1", fitment_status="002")) is None
    refs = [
        lv("R1", fitment_status="003"),
        lv("R2", fitment_status="001", baths=1),
        lv("P1", fitment_status="002"),
        lv("U1", fitment_status=None),
        lv("U2", fitment_status=""),
    ]
    grades = classify_all(TARGET, refs)
    assert {sample.listing_id for sample in grades} == {"R1", "R2"}


def test_rooms_zero_is_not_comparable() -> None:
    """测试 W:0 室不得互相成为 comparable。"""
    a = lv("A", rooms=0, halls=0, baths=0, area_sqm=29.0, monthly_rent_yuan=400.0)
    b = lv("B", rooms=0, halls=1, baths=0, area_sqm=19.0, monthly_rent_yuan=500.0,
           fitment_status="003")
    assert classify_comparable(a, b) is None
    assert resolve_community_benchmark(a, [b]).is_valid is False


def test_area_layout_sentinel_excludes_one_bed_260() -> None:
    """测试 X:1室 260㎡ 不得成为任何 1 室目标的 comparable。"""
    target = lv("T1", rooms=1, halls=1, baths=1, area_sqm=40.0, monthly_rent_yuan=2000.0)
    monster = lv("M", rooms=1, halls=1, baths=1, area_sqm=260.0, monthly_rent_yuan=8000.0)
    normal = lv("N", rooms=1, halls=1, baths=1, area_sqm=42.0, monthly_rent_yuan=2500.0)
    assert classify_comparable(target, monster) is None
    assert classify_comparable(target, normal) == "S"


# ------------------------------------------------------------------ Resolver


def _samples_for_vector(*, n_s=0, n_a=0, n_b=0, n_c=0, n_d=0, unit=100.0):
    """用面积/户型构造指定 S/A/B/C/D 计数,权重走真实 classify。"""
    refs: list[ListingView] = []
    for i in range(n_s):
        refs.append(lv(f"S{i}", area_sqm=128.0, monthly_rent_yuan=unit * 128.0))
    for i in range(n_a):
        refs.append(lv(f"A{i}", area_sqm=128.0 * 1.08, monthly_rent_yuan=unit * 128.0 * 1.08))
    for i in range(n_b):
        # 室等,厅差 2 → 修正后过不了 S/A,sim_area=1 ≥ 0.6 → B
        refs.append(lv(f"B{i}", halls=0, baths=2, area_sqm=128.0,
                       monthly_rent_yuan=unit * 128.0))
    for i in range(n_c):
        refs.append(lv(f"C{i}", rooms=4, area_sqm=128.0, monthly_rent_yuan=unit * 128.0))
    for i in range(n_d):
        # 室差 ≥2 → D。室数 6 不在面积哨兵表里,避免被误剔。
        refs.append(lv(f"D{i}", rooms=6, area_sqm=80.0, monthly_rent_yuan=unit * 80.0))
    return refs


def test_s_ge_3_is_s_only() -> None:
    result = resolve_community_benchmark(TARGET, _samples_for_vector(n_s=3))
    assert result.benchmark_mode == "S_ONLY"
    assert result.structure_cap == 100
    assert result.is_valid


def test_s2_blend_formula() -> None:
    """测试 B:S=2 + A=3 → S2_BLEND,80/20。"""
    refs = _samples_for_vector(n_s=2, n_a=3, unit=100.0)
    result = resolve_community_benchmark(TARGET, refs)
    assert result.benchmark_mode == "S2_BLEND"
    assert result.structure_cap == 100
    s_med = 100.0
    a_med = 100.0
    assert result.benchmark == pytest.approx(0.80 * s_med + 0.20 * a_med)


def test_s2_without_secondary_is_weak_not_history() -> None:
    """测试 C:S=2 无 A/B → S2_ONLY_WEAK,仍是 community。"""
    refs = _samples_for_vector(n_s=2, n_c=1)
    result = resolve_community_benchmark(TARGET, refs)
    assert result.benchmark_mode == "S2_ONLY_WEAK"
    assert result.peer_scope == "community"
    assert result.structure_cap == 70
    assert result.is_valid


def test_s1_blend() -> None:
    """测试 D:S=1 且 secondary ≥2 → S1_BLEND,cap 60。"""
    refs = _samples_for_vector(n_s=1, n_a=2)
    result = resolve_community_benchmark(TARGET, refs)
    assert result.benchmark_mode == "S1_BLEND"
    assert result.structure_cap == 60
    assert result.benchmark == pytest.approx(0.40 * 100.0 + 0.60 * 100.0)


def test_s1_without_ladder_is_invalid() -> None:
    """测试 E:S=1、secondary 不足、阶梯也不足 → INVALID。"""
    refs = _samples_for_vector(n_s=1, n_c=2, n_d=2)
    result = resolve_community_benchmark(TARGET, refs, CORRECTED)
    assert result.benchmark_mode == "INVALID"
    assert result.is_valid is False


def test_ns1_fallthrough_c_only_is_monotonic() -> None:
    """测试 V:龙湖梵城向量 1/1/0/25/18 → C_ONLY,不是 INVALID。"""
    with_s = _samples_for_vector(n_s=1, n_a=1, n_c=25, n_d=18)
    without_s = _samples_for_vector(n_s=0, n_a=1, n_c=25, n_d=18)
    got = resolve_community_benchmark(TARGET, with_s)
    base = resolve_community_benchmark(TARGET, without_s)
    assert got.benchmark_mode == "C_ONLY"
    assert got.structure_cap == 45
    assert got.is_valid and base.is_valid
    assert got.structure_cap >= base.structure_cap
    legacy = resolve_community_benchmark(TARGET, with_s, LEGACY)
    assert legacy.benchmark_mode == "INVALID"


def test_ns1_fallthrough_d_only_huaqiaocheng() -> None:
    """测试 V 第二向量:1/0/0/2/3 → D_ONLY。"""
    refs = _samples_for_vector(n_s=1, n_c=2, n_d=3)
    result = resolve_community_benchmark(TARGET, refs)
    assert result.benchmark_mode == "D_ONLY"
    assert result.is_valid


def test_eight_identical_targets_share_one_benchmark() -> None:
    """约束 3:同小区 8 套同价清水房 benchmark 逐位相同。"""
    refs = _samples_for_vector(n_s=0, n_a=1, n_b=1, n_d=6)
    targets = [
        lv(f"T{i}", monthly_rent_yuan=4000.0 + i)  # 目标价不同也不该改变基准
        for i in range(8)
    ]
    results = [resolve_community_benchmark(target, refs) for target in targets]
    assert len({r.benchmark_mode for r in results}) == 1
    assert len({r.benchmark for r in results}) == 1
    assert len({r.n_eff for r in results}) == 1
    two = [resolve_community_benchmark(target, refs) for target in targets[:2]]
    assert two[0].benchmark == results[0].benchmark
    assert two[1].n_eff == results[0].n_eff


def test_weighted_median_picks_half_weight() -> None:
    assert weighted_median([(10.0, 1.0), (20.0, 1.0)]) == 10.0
    assert weighted_median([(10.0, 1.0), (20.0, 3.0)]) == 20.0


def test_legacy_absolute_window_still_available() -> None:
    target = lv("T", rooms=1, halls=1, baths=1, area_sqm=30.0, monthly_rent_yuan=1500.0)
    cand = lv("R", rooms=1, halls=1, baths=1, area_sqm=36.0, monthly_rent_yuan=2000.0)
    # 相对: r=0.20 > 0.10,不是 S/A;绝对 6㎡ 落在 A(≤10)
    assert classify_comparable(target, cand, CORRECTED) == "B"
    assert classify_comparable(target, cand, LEGACY) == "A"
