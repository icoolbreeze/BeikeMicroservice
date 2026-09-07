"""`roughcast_ranked` 模块的纯函数单元测试。

不需要 SQLite 也不需要 FastAPI;只覆盖:归一化、过滤器、排序键稳定性、
`deals` 排除周边估算、quality 排序用 raw 而非整数化分等关键不变量。
"""

from __future__ import annotations

from app.application import roughcast_ranked as ranked


def _row(listing_id: str, **overrides) -> dict:
    defaults = {
        "listing_id": listing_id,
        "quality_status": "scored",
        "quality_score": 80,
        "quality_score_raw": 80.0,
        "quality_tier": None,
        "confidence_score": 80,
        "monthly_rent_yuan": 3000.0,
        "unit_rent": 30.0,
        "create_time": "2026-08-20T00:00:00+00:00",
        "last_seen_at": "2026-08-20T00:00:00+00:00",
    }
    return {**defaults, **overrides}


def test_normalize_sort_unknown_falls_back_to_quality() -> None:
    assert ranked.normalize_sort(None) == "quality"
    assert ranked.normalize_sort("") == "quality"
    assert ranked.normalize_sort("bogus") == "quality"
    assert ranked.normalize_sort("quality") == "quality"
    assert ranked.normalize_sort("LATEST") == "latest"


def test_normalize_group_unknown_falls_back_to_scored() -> None:
    assert ranked.normalize_group(None) == "scored"
    assert ranked.normalize_group("") == "scored"
    assert ranked.normalize_group("nearby_estimate") == "nearby_estimate"
    assert ranked.normalize_group("garbage") == "scored"


def test_resolve_group_deals_forces_scored() -> None:
    assert ranked.resolve_group(requested="nearby_estimate", deals=True) == "scored"
    assert ranked.resolve_group(requested="scored", deals=False) == "scored"
    assert ranked.resolve_group(requested="nearby_estimate", deals=False) == "nearby_estimate"


def test_apply_group_filter_is_strict_equality() -> None:
    rows = [
        _row("a", quality_status="scored"),
        _row("b", quality_status="nearby_estimate"),
        _row("c", quality_status="insufficient"),
    ]
    assert [r["listing_id"] for r in ranked.apply_group_filter(rows, "scored")] == ["a"]
    assert [r["listing_id"] for r in ranked.apply_group_filter(rows, "nearby_estimate")] == ["b"]
    assert [r["listing_id"] for r in ranked.apply_group_filter(rows, "insufficient")] == ["c"]


def test_apply_deals_filter_drops_nearby_even_with_high_score() -> None:
    rows = [
        _row("a", quality_score_raw=80.0, confidence_score=80, quality_status="scored"),
        _row("b", quality_score_raw=95.0, confidence_score=90,
             quality_status="nearby_estimate"),
    ]
    out = ranked.apply_deals_filter(rows, deals=True, group="scored")
    assert [r["listing_id"] for r in out] == ["a"]


def test_apply_deals_filter_requires_score_and_confidence_thresholds() -> None:
    rows = [
        _row("a", quality_score=80, quality_score_raw=80.0, confidence_score=80,
             quality_status="scored"),
        _row("b", quality_score=80, quality_score_raw=80.0, confidence_score=60,
             quality_status="scored"),
        _row("c", quality_score=70, quality_score_raw=70.0, confidence_score=85,
             quality_status="scored"),
    ]
    out = ranked.apply_deals_filter(rows, deals=True, group="scored")
    assert [r["listing_id"] for r in out] == ["a"]


def test_apply_deals_filter_uses_integer_quality_score_not_raw() -> None:
    """设计 + brief + score-review 一致:`quality_score >= 75`(整数),不是 raw。

    关键边界:整数 75 达标;即便 raw 跌到 74.6 仍应视为达标——这是设计
    上承认的截断;反过来 raw=95.0 但整数 74 不达标——deals 不能放进去。
    周边估算 nearby_estimate 不能进 deals(没有 confidence 可言)。
    """
    rows = [
        _row("a", quality_score=75, quality_score_raw=74.6, confidence_score=80,
             quality_status="scored"),
        _row("b", quality_score=95, quality_score_raw=95.0, confidence_score=80,
             quality_status="nearby_estimate"),
    ]
    out = ranked.apply_deals_filter(rows, deals=True, group="scored")
    assert [r["listing_id"] for r in out] == ["a"]

    # 反向:整数 74 不到门槛,raw=95 也不进
    rows2 = [
        _row("x", quality_score=74, quality_score_raw=95.0, confidence_score=80,
             quality_status="scored"),
    ]
    out2 = ranked.apply_deals_filter(rows2, deals=True, group="scored")
    assert out2 == []


def test_apply_min_confidence_keeps_all_when_zero() -> None:
    rows = [_row("a", confidence_score=10), _row("b", confidence_score=80)]
    out = ranked.apply_min_confidence(rows, 0)
    assert [r["listing_id"] for r in out] == ["a", "b"]


def test_apply_min_confidence_drops_below_threshold() -> None:
    rows = [_row("a", confidence_score=50), _row("b", confidence_score=80)]
    out = ranked.apply_min_confidence(rows, 60)
    assert [r["listing_id"] for r in out] == ["b"]


def test_sort_rows_quality_uses_raw_not_integer() -> None:
    """raw=82.9 应该排在 raw=80.1 之前,即便整数都是 83 / 80。

    整数化会丢精度,质量极接近的两套会随机反序;raw DESC 才是 ground truth。
    """
    rows = [
        _row("a", quality_score=83, quality_score_raw=82.9, confidence_score=80),
        _row("b", quality_score=80, quality_score_raw=80.1, confidence_score=80),
    ]
    out = ranked.sort_rows(rows, "quality")
    assert [r["listing_id"] for r in out] == ["a", "b"]


def test_sort_rows_quality_uses_confidence_as_tie_breaker() -> None:
    rows = [
        _row("a", quality_score_raw=80.0, confidence_score=60),
        _row("b", quality_score_raw=80.0, confidence_score=90),
    ]
    out = ranked.sort_rows(rows, "quality")
    assert [r["listing_id"] for r in out] == ["b", "a"]


def test_sort_rows_quality_pushes_nulls_last() -> None:
    rows = [
        _row("a", quality_score_raw=None, quality_score=None, quality_status="data_error"),
        _row("b", quality_score_raw=70.0, quality_score=70, quality_status="scored"),
    ]
    out = ranked.sort_rows(rows, "quality")
    assert [r["listing_id"] for r in out] == ["b", "a"]


def test_sort_rows_rent_asc_pushes_nulls_last() -> None:
    rows = [
        _row("a", monthly_rent_yuan=5000.0),
        _row("b", monthly_rent_yuan=2000.0),
        _row("c", monthly_rent_yuan=None),
    ]
    out = ranked.sort_rows(rows, "rent_asc")
    assert [r["listing_id"] for r in out] == ["b", "a", "c"]


def test_sort_rows_latest_orders_by_create_time_desc() -> None:
    """`latest` = `create_time DESC`,回退 `last_seen_at DESC`,`listing_id ASC`;缺时间戳的排最后。

    旧版用 ASC 排,会把更早的放第一,这条要锁住 DESC。create_time 缺失时
    退到 last_seen_at。
    """
    rows = [
        _row("a", create_time="2026-08-20T00:00:00+00:00",
             last_seen_at="2026-08-20T00:00:00+00:00"),
        _row("b", create_time="2026-08-24T00:00:00+00:00",
             last_seen_at="2026-08-24T00:00:00+00:00"),
    ]
    out = ranked.sort_rows(rows, "latest")
    assert [r["listing_id"] for r in out] == ["b", "a"]


def test_sort_rows_latest_falls_back_to_last_seen_and_pushes_nulls_last() -> None:
    """create_time 为空时退到 last_seen_at DESC;两者都缺则排最后。"""
    rows = [
        _row("a", create_time=None, last_seen_at="2026-08-15T00:00:00+00:00"),
        _row("b", create_time="2026-08-24T00:00:00+00:00", last_seen_at=None),
        _row("c", create_time=None, last_seen_at=None),
    ]
    out = ranked.sort_rows(rows, "latest")
    # b 是最新(create_time=2026-08-24);a 走 last_seen=2026-08-15;c 没时间排最后
    assert [r["listing_id"] for r in out] == ["b", "a", "c"]


def test_sort_rows_latest_uses_listing_id_as_asc_tie_breaker() -> None:
    """并列时间 DESC 时,`listing_id` 应保持 ASC(不能被 `reverse=True` 翻成 DESC)。"""
    rows = [
        _row("zzz", create_time="2026-08-24T00:00:00+00:00"),
        _row("aaa", create_time="2026-08-24T00:00:00+00:00"),
        _row("mmm", create_time="2026-08-24T00:00:00+00:00"),
    ]
    out = ranked.sort_rows(rows, "latest")
    assert [r["listing_id"] for r in out] == ["aaa", "mmm", "zzz"]


def test_paginate_total_and_has_more() -> None:
    rows = [_row(f"id-{i}") for i in range(5)]
    sliced, total, has_more = ranked.paginate(rows, page=1, page_size=2)
    assert total == 5
    assert has_more is True
    assert [r["listing_id"] for r in sliced] == ["id-0", "id-1"]
    sliced, total, has_more = ranked.paginate(rows, page=3, page_size=2)
    assert total == 5
    assert has_more is False
    assert [r["listing_id"] for r in sliced] == ["id-4"]
