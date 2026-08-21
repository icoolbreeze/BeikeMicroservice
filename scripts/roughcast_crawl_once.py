"""手动跑一轮清水房采集（队列 A，只采集不评分）。

`services/store_media/docs/roughcast-quality-ranking.md` 第 1 期的唯一触发方式:
常驻线程默认关闭（`SM_ROUGHCAST_CRAWL_ENABLED=0`）,头几天由人手工跑这个脚本,
确认上游流量曲线之后再打开线程。

**这个脚本会向真实上游发请求。** 一轮约 66–76 次(页数由第 1 页的 `total` 现算,
不是写死的),受 `SM_ROUGHCAST_DAILY_REQUEST_CAP`(默认 260)硬顶约束,
且当日已花次数是从 `roughcast_crawl_log` 数出来的——重启不会忘记今天的账。
按 20 秒最小间隔 + 页间随机停顿,一轮大约 40–90 分钟,期间不要打断。

先干跑一遍看装配参数(零上游请求):

    python scripts/roughcast_crawl_once.py --dry-run

真跑:

    python scripts/roughcast_crawl_once.py

`SM_CRM_CONNECTOR_BASE_URL` 可指向本地假 connector 做联调;默认打
`http://127.0.0.1:8020`,也就是用户自己在跑的那个实例。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_crawler import (  # noqa: E402
    build_crawler,
    crawl_config_from_settings,
)
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.roughcast_throttle import RoughcastThrottle  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="手动跑一轮清水房采集（队列 A）")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印装配参数与当日已花次数,不发任何上游请求",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    database = Database(settings.database_path)
    database.initialize()
    config = crawl_config_from_settings(settings)

    throttle = RoughcastThrottle(
        RoughcastRepository(database),
        daily_budget=config.daily_request_cap,
        min_interval_seconds=config.min_request_interval_seconds,
        timezone=config.timezone,
    )
    print(f"connector : {settings.crm_connector_base_url}")
    print(f"数据库     : {settings.database_path}")
    print(f"每页       : {config.page_size} 条")
    print(f"当日硬顶   : {config.daily_request_cap} 次")
    print(f"当日已花   : {throttle.spent_today()} 次（从 crawl_log 数出）")
    print(f"最小间隔   : {config.min_request_interval_seconds} 秒")
    print(f"采集窗口   : {config.window_start}–{config.window_end}"
          f"（UTC+{config.utc_offset_hours}）")
    print(f"重试预留   : {config.retry_reserve} 次")

    if args.dry_run:
        print("\n--dry-run:未发任何上游请求。")
        return 0

    outcome = build_crawler(settings, database).run_once()

    print(f"\nrun {outcome.run_id} -> {outcome.status}")
    print(f"翻页       : {outcome.pages_done}/{outcome.pages_expected}")
    print(f"上游请求   : {outcome.requests} 次")
    if outcome.status == "COMPLETE":
        print(f"发布       : {outcome.published} 套清水房")
        print(f"新快照     : {outcome.snapshots_inserted} 行")
        print(f"判下架     : {outcome.deactivated} 套")
        return 0
    # ABORTED / FAILED 一律不发布,listing_current 保持上一轮 COMPLETE 的状态。
    print(f"原因       : {outcome.reason}")
    print("未发布:listing_current / listing_snapshot 均未改动。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
