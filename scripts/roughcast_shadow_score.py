"""第 4 期 Shadow Run:算分入库,不上前端。零上游请求。

    python scripts/roughcast_shadow_score.py --dry-run
    python scripts/roughcast_shadow_score.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_scorer import run_shadow  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="清水房优质指数 Shadow Run")
    parser.add_argument("--dry-run", action="store_true", help="算分不写库")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    repository = RoughcastRepository(database)
    outcome = run_shadow(repository, persist=not args.dry_run)

    scores = list(outcome.scores)
    print(f"数据库     : {settings.database_path}")
    print(f"模式       : {'dry-run(不写库)' if args.dry_run else '入库'}")
    if outcome.run_id is not None:
        print(f"score_run  : {outcome.run_id}")
    print(f"δ          : {outcome.delta.value:.4f}  "
          f"{'(fallback) ' if outcome.delta.used_fallback else ''}"
          f"{outcome.delta.note}")
    print(f"结果       : scored={outcome.scored}  nearby={outcome.nearby}  "
          f"insufficient={outcome.insufficient}  data_error={outcome.data_error}  "
          f"extreme={outcome.extreme}")
    ranked = [row for row in scores if row.quality_status == "scored" and row.city_rank]
    ranked.sort(key=lambda row: row.city_rank or 0)
    if ranked:
        print("主榜 Top 10")
        for row in ranked[:10]:
            print(f"  #{row.city_rank:<4} {row.listing_id}  {row.quality_score}分  "
                  f"conf={row.confidence_score}  {row.benchmark_mode}  {row.reason[:80]}")
        print("主榜 最低 10")
        for row in ranked[-10:]:
            print(f"  #{row.city_rank:<4} {row.listing_id}  {row.quality_score}分  "
                  f"conf={row.confidence_score}  {row.benchmark_mode}")
    extremes = [row for row in ranked if row.extreme_price]
    print(f"待人工核 extreme_price: {len(extremes)} 套（主榜内）")
    for row in extremes[:10]:
        print(f"  #{row.city_rank} {row.listing_id}  {row.quality_score}分  adv={row.advantage:.3f}")
    if args.dry_run:
        print("\n--dry-run:未写 listing_scores。")
    else:
        print("\n已写入 roughcast_listing_scores。前端 /roughcast.html 未接此表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
