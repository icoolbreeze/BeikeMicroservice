"""第 4 期评分编排:回退链 + δ 标定 + Shadow Run。

零上游请求。读 P / 指针 R,写 `roughcast_listing_scores`。不上前端。
R 主池只取精装 `003`,简装 `001` 作次级回退(第七章第 9 条全量结论)。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Sequence

from app.domain.roughcast_benchmark import (
    BenchmarkResult,
    ListingView,
    is_data_error,
    resolve_community_benchmark,
)
from app.domain.roughcast_score import (
    DELTA_FALLBACK,
    K_SCALE,
    MODEL_VERSION,
    NEARBY_MIN_NEIGHBORS,
    NEARBY_SPREAD_MAX,
    PRIMARY_FITMENT,
    SECONDARY_FITMENT,
    SOURCE_CAP,
    STRONG_NEARBY_MODES,
    DeltaEstimate,
    ScoreResult,
    apply_score,
    assign_city_ranks,
    calibrate_delta,
)
from app.infrastructure.roughcast_repository import COMPLETE, PARTIAL, RoughcastRepository

logger = logging.getLogger(__name__)

FITMENT_ORDER = (PRIMARY_FITMENT, SECONDARY_FITMENT)


@dataclass
class ScoreContext:
    refs: dict[str, dict[str, list[ListingView]]]
    community_run: dict[str, int | None]
    community_biz: dict[str, str | None]
    biz_members: dict[str, list[str]]


@dataclass
class ShadowOutcome:
    run_id: int | None
    delta: DeltaEstimate
    scores: tuple[ScoreResult, ...]
    scored: int
    nearby: int
    insufficient: int
    data_error: int
    extreme: int


def load_score_context(repository: RoughcastRepository) -> tuple[list[ListingView], ScoreContext]:
    targets = [_target_from_row(row) for row in repository.list_active_listings()]
    refs: dict[str, dict[str, list[ListingView]]] = defaultdict(lambda: defaultdict(list))
    for row in repository.list_pointer_references():
        listing = _ref_from_row(row, age_days=0)
        if listing.community_id and listing.fitment_status:
            refs[listing.community_id][listing.fitment_status].append(listing)
    community_run: dict[str, int | None] = {}
    community_biz: dict[str, str | None] = {}
    biz_members: dict[str, list[str]] = defaultdict(list)
    for row in repository.list_communities_meta():
        cid = str(row["id"])
        community_run[cid] = row["reference_run_id"]
        biz = row["bizcircle"]
        community_biz[cid] = biz
        if biz:
            biz_members[str(biz)].append(cid)
    ctx = ScoreContext(
        refs={key: dict(value) for key, value in refs.items()},
        community_run=community_run,
        community_biz=community_biz,
        biz_members=dict(biz_members),
    )
    return targets, ctx


def resolve_listing(target: ListingView, ctx: ScoreContext) -> tuple[BenchmarkResult, dict]:
    """回退链:本小区 003 → 本小区 001 → 周边。历史批次首轮尚无,接口留好。"""
    extra: dict = {
        "source_cap": 100,
        "freshness": 100.0,
        "reference_age_days": 0,
        "reference_run_id": ctx.community_run.get(target.community_id or ""),
        "r_fitment": None,
        "nearby_k": None,
        "nearby_spread": None,
        "nearby_pool": None,
    }
    if is_data_error(target):
        return BenchmarkResult(
            is_valid=False, benchmark=None, benchmark_mode="INVALID",
            n_s=0, n_a=0, n_b=0, n_c=0, n_d=0, n_eff=0.0, structure_cap=0,
            peer_scope="community",
        ), extra

    cid = target.community_id or ""
    local = ctx.refs.get(cid, {})
    for fitment in FITMENT_ORDER:
        result = resolve_community_benchmark(target, local.get(fitment, ()))
        if result.is_valid:
            extra["r_fitment"] = fitment
            extra["source_cap"] = SOURCE_CAP["community"]
            return result, extra

    nearby = _resolve_nearby(target, ctx)
    if nearby is not None:
        result, spread, count, pool = nearby
        extra.update({
            "source_cap": SOURCE_CAP["nearby"],
            "nearby_k": count,
            "nearby_spread": spread,
            "nearby_pool": pool,
            "r_fitment": PRIMARY_FITMENT,
        })
        return result, extra

    extra["source_cap"] = 0
    return BenchmarkResult(
        is_valid=False, benchmark=None, benchmark_mode="INVALID",
        n_s=0, n_a=0, n_b=0, n_c=0, n_d=0, n_eff=0.0, structure_cap=0,
        peer_scope="community",
    ), extra


def score_all(
    targets: Sequence[ListingView], ctx: ScoreContext, *,
    delta: float, k: float = K_SCALE,
) -> list[ScoreResult]:
    scored: list[ScoreResult] = []
    for target in targets:
        resolved, extra = resolve_listing(target, ctx)
        scored.append(apply_score(target, resolved, delta=delta, k=k, **extra))
    return assign_city_ranks(scored)


def calibrate_from_resolved(
    pairs: Sequence[tuple[ListingView, tuple[BenchmarkResult, dict]]],
) -> DeltaEstimate:
    rows: list[tuple[str, float, float, str]] = []
    for target, (resolved, _extra) in pairs:
        unit = target.resolved_unit_rent
        if (
            resolved.is_valid and resolved.peer_scope == "community"
            and resolved.benchmark and unit and target.community_id
        ):
            rows.append((target.community_id, unit, resolved.benchmark, resolved.benchmark_mode))
    return calibrate_delta(rows, fallback=DELTA_FALLBACK)


def calibrate_from_targets(targets: Sequence[ListingView], ctx: ScoreContext) -> DeltaEstimate:
    pairs = [(target, resolve_listing(target, ctx)) for target in targets]
    return calibrate_from_resolved(pairs)


def run_shadow(
    repository: RoughcastRepository, *,
    persist: bool = True, k: float = K_SCALE,
) -> ShadowOutcome:
    targets, ctx = load_score_context(repository)
    logger.info("评分载入 P=%s 小区=%s", len(targets), len(ctx.community_run))
    resolved_pairs = [(target, resolve_listing(target, ctx)) for target in targets]
    delta = calibrate_from_resolved(resolved_pairs)
    logger.info("δ=%s (%s)", delta.value, delta.note)
    scores = assign_city_ranks([
        apply_score(target, resolved, delta=delta.value, k=k, **extra)
        for target, (resolved, extra) in resolved_pairs
    ])
    counts = _counts(scores)
    run_id = None
    if persist:
        listing_run = repository.latest_published_run("A")
        listing_run_id = int(listing_run["id"]) if listing_run else None
        run_id = repository.start_score_run(
            model_version=MODEL_VERSION, delta_version=delta.version,
            k_scale=k, listing_run_id=listing_run_id,
        )
        try:
            repository.complete_score_run(
                run_id, scores, delta_value=delta.value, delta_note=delta.note,
                model_version=MODEL_VERSION, delta_version=delta.version,
            )
        except Exception:
            repository.fail_score_run(run_id, "complete_score_run failed")
            raise
        logger.info("Shadow Run %s COMPLETE scored=%s nearby=%s insufficient=%s",
                    run_id, counts["scored"], counts["nearby"], counts["insufficient"])
    return ShadowOutcome(
        run_id=run_id, delta=delta, scores=tuple(scores),
        scored=counts["scored"], nearby=counts["nearby"],
        insufficient=counts["insufficient"], data_error=counts["data_error"],
        extreme=counts["extreme"],
    )


def _resolve_nearby(
    target: ListingView, ctx: ScoreContext,
) -> tuple[BenchmarkResult, float, int, list[dict]] | None:
    biz = ctx.community_biz.get(target.community_id or "") or target.bizcircle
    if not biz:
        return None
    members = ctx.biz_members.get(biz, [])
    strong: list[tuple[str, BenchmarkResult]] = []
    for neighbor_id in members:
        if neighbor_id == target.community_id:
            continue
        local = ctx.refs.get(neighbor_id, {})
        result = None
        for fitment in FITMENT_ORDER:
            result = resolve_community_benchmark(
                target, local.get(fitment, ()), require_same_community=False,
            )
            if result.is_valid:
                break
        if result is None or not result.is_valid:
            continue
        if result.benchmark_mode in STRONG_NEARBY_MODES and result.benchmark:
            strong.append((neighbor_id, result))
    if len(strong) < NEARBY_MIN_NEIGHBORS:
        return None
    values = [item[1].benchmark for item in strong if item[1].benchmark is not None]
    mid = float(median(values))
    if mid <= 0:
        return None
    spread = (max(values) - min(values)) / mid
    if spread > NEARBY_SPREAD_MAX:
        return None
    pool = [
        {"community_id": cid, "benchmark": res.benchmark, "mode": res.benchmark_mode}
        for cid, res in strong
    ]
    result = BenchmarkResult(
        is_valid=True, benchmark=mid, benchmark_mode="NEARBY",
        n_s=0, n_a=0, n_b=0, n_c=0, n_d=0, n_eff=float(len(strong)),
        structure_cap=100, peer_scope="nearby",
        pool_listing_ids=(), pool_samples=(),
    )
    return result, spread, len(strong), pool


def _counts(scores: Sequence[ScoreResult]) -> dict[str, int]:
    return {
        "scored": sum(1 for row in scores if row.quality_status == "scored"),
        "nearby": sum(1 for row in scores if row.quality_status == "nearby_estimate"),
        "insufficient": sum(1 for row in scores if row.quality_status == "insufficient"),
        "data_error": sum(1 for row in scores if row.quality_status == "data_error"),
        "extreme": sum(1 for row in scores if row.extreme_price == 1),
    }


def _target_from_row(row) -> ListingView:
    return ListingView(
        listing_id=str(row["listing_id"]),
        community_id=row["community_id"],
        community_name=row["community_name"] if "community_name" in row.keys() else None,
        rent_mode=row["rent_mode"],
        rooms=row["rooms"],
        halls=row["halls"],
        baths=row["baths"],
        area_sqm=row["area_sqm"],
        monthly_rent_yuan=row["monthly_rent_yuan"],
        fitment_status=row["fitment_status"],
        bizcircle=row["bizcircle"] if "bizcircle" in row.keys() else None,
        last_seen_run_id=row["last_seen_run_id"] if "last_seen_run_id" in row.keys() else None,
        age_days=0,
    )


def _ref_from_row(row, *, age_days: int) -> ListingView:
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
        age_days=age_days,
        captured_at=row["captured_at"] if "captured_at" in row.keys() else None,
        run_id=row["run_id"] if "run_id" in row.keys() else None,
    )
