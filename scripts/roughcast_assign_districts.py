"""把小区归到行政区并写入本地库。

先拉一次 CRM 筛选目录（区 → 商圈），再按每个小区已有的 bizcircle 落库。
之后查 `roughcast_communities.district` 或 `roughcast_community_district`，
不必再打 CRM。

    python scripts/roughcast_assign_districts.py --dry-run
    python scripts/roughcast_assign_districts.py
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_districts import persist_districts  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.district_catalog import DistrictCatalogFetcher  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="把小区行政区写入本地库")
    parser.add_argument("--dry-run", action="store_true", help="只打印分类,不写库")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    if not settings.database_path.exists():
        print(f"数据库不存在:{settings.database_path}")
        return 1
    database = Database(settings.database_path)
    database.initialize()
    repository = RoughcastRepository(database)
    catalog = DistrictCatalogFetcher(settings.crm_connector_base_url).latest()
    if not catalog.bizcircle_to_districts:
        print("没有拉到商圈目录。确认 crm_connector 在跑。")
        return 1
    captured_at = datetime.now(UTC).isoformat()
    communities = repository.list_communities_for_district()
    print(f"数据库     : {settings.database_path}")
    print(f"目录商圈   : {len(catalog.bizcircle_to_districts)} 个 / 区 {len(catalog.districts)} 个")
    print(f"待归类小区 : {len(communities)}")
    if args.dry_run:
        from collections import Counter
        from app.infrastructure.district_catalog import classify_bizcircle
        counts = Counter(
            classify_bizcircle(catalog, row["bizcircle"])[2] for row in communities
        )
        print(f"dry-run    : unique={counts['unique']}  ambiguous={counts['ambiguous']}  "
              f"unknown={counts['unknown']}")
        return 0
    report = persist_districts(repository, catalog, captured_at=captured_at)
    print(f"已写入     : unique={report.unique}  ambiguous={report.ambiguous}  "
          f"unknown={report.unknown}  catalog_pairs={report.catalog_pairs}")
    print("查询示例   : SELECT id, name, bizcircle, district FROM roughcast_communities "
          "WHERE district='成华';")
    print("含跨区商圈 : SELECT c.id, c.name, d.district FROM roughcast_communities c "
          "JOIN roughcast_community_district d ON d.community_id=c.id WHERE d.district='成华';")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
