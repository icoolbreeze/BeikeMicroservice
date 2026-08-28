"""用百度地点检索给小区打行政区并写入坐标。

小区位置不会变：已标记 `district_source='baidu'` 的行默认跳过。
需要 SM_BAIDU_MAP_AK 或 BAIDU_MAP_AK。

    python scripts/roughcast_mark_districts.py --limit 5
    python scripts/roughcast_mark_districts.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_baidu_districts import (  # noqa: E402
    CommunityGeocoder,
    mark_communities_with_baidu,
)
from app.infrastructure.baidu_map_client import BaiduMapClient  # noqa: E402
from app.infrastructure.crm_map_geocoder import CrmMapGeocoder  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="用百度地图给小区标记行政区")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少个未标记小区")
    parser.add_argument("--sleep", type=float, default=0.35, help="两次检索间隔秒")
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
    pending = repository.list_communities_pending_baidu()
    baidu = BaiduMapClient(settings.baidu_map_ak, retries=2) if settings.baidu_map_ak else None
    geocoder = CommunityGeocoder(
        baidu=baidu,
        crm=CrmMapGeocoder(settings.crm_connector_base_url),
    )
    print(f"数据库     : {settings.database_path}")
    print(f"已持久标记 : {repository.count_baidu_marked_communities()}")
    print(f"待标记     : {len(pending)}" + (f" (本次 {args.limit})" if args.limit else ""))
    print(f"通道       : {'百度地点检索 + ' if baidu else ''}贝壳地图联想(BD-09)")
    report = mark_communities_with_baidu(
        repository,
        geocoder,
        limit=args.limit,
        sleep_seconds=max(args.sleep, 0.0),
    )
    print(f"结果       : marked={report.marked}  baidu={report.baidu_marked}  "
          f"beike_map={report.beike_marked}  missed={report.missed}  "
          f"errors={report.errors}  skipped_already={report.skipped}")
    print("查询示例   : SELECT id, name, district, latitude, longitude "
          "FROM roughcast_communities WHERE district_source='baidu' AND district='成华';")
    return 0 if report.errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
