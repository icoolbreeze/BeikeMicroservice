"""手动跑队列 B（小区参考集。第 3 期,只采集不评分）。

第一次请用 `--full-sweep`:把还没有参考集的小区一次性扫完。每小区单独收尾,
中断后续跑会跳过已经 COMPLETE 的小区。

    python scripts/roughcast_queue_b_once.py --full-sweep --dry-run
    python scripts/roughcast_queue_b_once.py --full-sweep

日常半月轮转(每轮按预算取一批):

    python scripts/roughcast_queue_b_once.py --limit 60

看库存(只读,零上游请求):

    python scripts/roughcast_queue_b_once.py --status
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_crawler import crawl_config_from_settings  # noqa: E402
from app.application.roughcast_queue_b import (  # noqa: E402
    FULL_SWEEP_REQUEST_CAP,
    build_queue_b_crawler,
    full_sweep_config,
)
from app.application.roughcast_status import (  # noqa: E402
    DEFAULT_RUN_LIMIT,
    build_status_report,
    render_status,
)
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.roughcast_throttle import RoughcastThrottle  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="手动跑一轮清水房队列 B（小区参考集）")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印装配参数与待刷新小区数,不发任何上游请求",
    )
    parser.add_argument(
        "--full-sweep", action="store_true",
        help="一次性扫完所有还没有参考集的小区(每小区单独收尾,中断可续跑)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="本轮最多刷新多少个小区。与 --full-sweep 互斥;默认按队列顺序取全部可排期的",
    )
    parser.add_argument(
        "--status", nargs="?", type=int, const=DEFAULT_RUN_LIMIT, default=None,
        metavar="N",
        help="只读:打印近 N 轮摘要与参考集库存,不发任何上游请求",
    )
    args = parser.parse_args()
    if args.full_sweep and args.limit is not None:
        parser.error("--full-sweep 与 --limit 不能一起用")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    base_config = crawl_config_from_settings(settings)
    config = full_sweep_config(base_config) if args.full_sweep else base_config

    if args.status is not None:
        return print_status(settings, config, limit=args.status)

    database = Database(settings.database_path)
    database.initialize()
    repository = RoughcastRepository(database)
    inventory = repository.queue_b_inventory()
    throttle = RoughcastThrottle(
        repository,
        daily_budget=config.daily_request_cap,
        min_interval_seconds=config.min_request_interval_seconds,
        timezone=config.timezone,
    )

    pending = inventory["unreferenced"] if args.full_sweep else (
        args.limit if args.limit is not None else inventory["searchable"]
    )
    estimated_pages = max(int(pending * 1.5), pending)
    estimated_seconds = estimated_pages * config.min_request_interval_seconds

    print(f"connector : {settings.crm_connector_base_url}")
    print(f"数据库     : {settings.database_path}")
    print(f"模式       : {'全量一次性' if args.full_sweep else '轮转一批'}")
    print(f"小区总计   : {inventory['communities']}")
    print(f"可搜       : {inventory['searchable']}")
    print(f"待刷新     : {inventory['unreferenced']}")
    print(f"已有参考集 : {inventory['referenced']}")
    print(f"无 resblock: {inventory['skipped_no_resblock']}（跳过）")
    print(f"参考快照   : {inventory['reference_rows']} 行")
    print(f"当日硬顶   : {config.daily_request_cap} 次"
          + (f"（全量已抬到 ≥{FULL_SWEEP_REQUEST_CAP}）" if args.full_sweep else ""))
    print(f"当日已花   : {throttle.spent_today()} 次（从 crawl_log 数出）")
    print(f"最小间隔   : {config.min_request_interval_seconds} 秒")
    print(f"采集窗口   : {'关闭（全量模式）' if args.full_sweep else f'{config.window_start}–{config.window_end}'}")
    print(f"本轮预估   : {pending} 个小区 / ~{estimated_pages} 次请求 / "
          f"约 {estimated_seconds / 3600:.1f} 小时")

    if args.dry_run:
        print("\n--dry-run:未发任何上游请求。")
        return 0

    crawler = build_queue_b_crawler(settings, database, full_sweep=args.full_sweep)
    if args.full_sweep:
        outcome = crawler.run_sweep()
        print(f"\n全量 -> {outcome.status}")
        print(f"小区       : 成功 {outcome.communities_success} / "
              f"失败 {outcome.communities_failed} / "
              f"中止 {outcome.communities_aborted} / "
              f"目标 {outcome.communities_targeted}")
        print(f"翻页       : {outcome.pages_done}")
        print(f"上游请求   : {outcome.requests} 次")
        print(f"参考快照   : {outcome.reference_rows} 行"
              f"（其中非毛坯 R {outcome.reference_r_rows}）")
        if outcome.stopped_reason:
            print(f"停在       : {outcome.stopped_reason}")
            print("已 COMPLETE 的小区指针保留,再跑一次 --full-sweep 会续上。")
            return 1
        if outcome.communities_success == 0 and outcome.communities_targeted:
            return 1
        return 0

    outcome = crawler.run_once(limit=args.limit)
    if outcome is None:
        print("\n没有可搜的小区。先跑队列 A 写出 roughcast_communities。")
        return 1
    print(f"\nrun {outcome.run_id} -> {outcome.status}")
    print(f"翻页       : {outcome.pages_done}/{outcome.pages_expected}")
    print(f"上游请求   : {outcome.requests} 次")
    print(f"参考快照   : {outcome.reference_rows} 行"
          f"（其中非毛坯 R {outcome.reference_r_rows}）")
    if outcome.reason:
        print(f"原因       : {outcome.reason}")
    return 0 if outcome.status in {"COMPLETE", "PARTIAL"} else 1


def print_status(settings, config, *, limit: int) -> int:
    if not settings.database_path.exists():
        print(f"数据库不存在:{settings.database_path}")
        return 1
    repository = RoughcastRepository(Database(settings.database_path))
    try:
        report = build_status_report(repository, config, limit=limit)
    except sqlite3.OperationalError as exc:
        print(f"读不到采集库表:{exc}")
        return 1
    print(render_status(report, config, database_path=settings.database_path))
    inventory = repository.queue_b_inventory()
    print()
    print("队列 B 库存")
    print(f"  可搜小区 {inventory['searchable']} / 已刷新 {inventory['referenced']} / "
          f"待刷新 {inventory['unreferenced']} / 无 resblock {inventory['skipped_no_resblock']}")
    print(f"  参考快照 {inventory['reference_rows']} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
