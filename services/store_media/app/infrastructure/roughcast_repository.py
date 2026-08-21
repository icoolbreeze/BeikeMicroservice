"""清水房采集的仓储层（`docs/roughcast-quality-ranking.md` §4.2 / §4.5）。

这一层持有 run 状态机的**事务边界**。唯一需要记住的规则：
`COMPLETE` 是唯一允许发布的终态，而发布的全部动作都在 `complete_run()` 的
一个事务里；`ABORTED` / `FAILED` 的 run 对 `listing_current` 与
`listing_snapshot` 一个字节都碰不到（它们的数据止步于 `crawl_stage`）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Callable, Iterable, Sequence

from app.domain.roughcast import BUSINESS_FIELDS, RoughcastRow, business_values, content_hash
from app.infrastructure.database import Database

logger = logging.getLogger(__name__)

RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
ABORTED = "ABORTED"
FAILED = "FAILED"

TARGET_FITMENT = "002"  # 毛坯。'001' 简装 / '003' 精装 属参考集 R,不进被排序集 P

_BUSINESS_SQL = ", ".join(BUSINESS_FIELDS)
_BUSINESS_PLACEHOLDERS = ", ".join("?" for _ in BUSINESS_FIELDS)
_BUSINESS_EXCLUDED = ", ".join(f"{name} = excluded.{name}" for name in BUSINESS_FIELDS)


class RunStateError(RuntimeError):
    """试图对一个不处于 RUNNING 的 run 做状态转移，或 COMPLETE 判据不满足。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RoughcastRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = _utcnow):
        self.database = database
        self._clock = clock

    def _now(self) -> str:
        return self._clock().isoformat()

    # ---------------------------------------------------------------- runs

    def start_run(self, queue: str) -> int:
        with self.database.connect() as db:
            cursor = db.execute(
                "INSERT INTO roughcast_crawl_runs (queue, status, started_at) VALUES (?, ?, ?)",
                (queue, RUNNING, self._now()),
            )
            run_id = int(cursor.lastrowid)
        return run_id

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()

    def record_progress(self, run_id: int, *, pages_done: int | None = None,
                        pages_expected: int | None = None,
                        upstream_total: int | None = None) -> None:
        assignments: list[str] = []
        values: list[object] = []
        for column, value in (("pages_done", pages_done),
                              ("pages_expected", pages_expected),
                              ("upstream_total", upstream_total)):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        if not assignments:
            return
        values.append(run_id)
        with self.database.connect() as db:
            db.execute(
                f"UPDATE roughcast_crawl_runs SET {', '.join(assignments)} WHERE id = ?", values
            )

    def abort_run(self, run_id: int, reason: str) -> None:
        self._finish(run_id, ABORTED, reason)

    def fail_run(self, run_id: int, reason: str) -> None:
        self._finish(run_id, FAILED, reason)

    def _finish(self, run_id: int, status: str, reason: str) -> None:
        with self.database.connect() as db:
            cursor = db.execute(
                "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ?, abort_reason = ? "
                "WHERE id = ? AND status = ?",
                (status, self._now(), reason, run_id, RUNNING),
            )
            moved = cursor.rowcount
        if moved != 1:
            # 不抬升成异常:收尾路径上再抛一次会盖掉真正的失败原因。
            logger.warning("run %s 已不处于 RUNNING,跳过 %s 转移", run_id, status)

    def reap_stale_runs(self, *, max_age: timedelta) -> list[int]:
        """把超时仍是 RUNNING 的 run 判 FAILED,覆盖「进程退出」那条转移。

        进程被 kill 时没有机会写终态,残留的 RUNNING 行会让「最新 COMPLETE run」
        之外多出一个永久悬挂的 run。启动时清理一次。
        """
        cutoff = (self._clock() - max_age).isoformat()
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT id FROM roughcast_crawl_runs WHERE status = ? AND started_at < ?",
                (RUNNING, cutoff),
            ).fetchall()
            run_ids = [int(row["id"]) for row in rows]
            if run_ids:
                db.executemany(
                    "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ?, "
                    "abort_reason = ? WHERE id = ?",
                    [(FAILED, self._now(), "stale_running_run_reaped", rid) for rid in run_ids],
                )
        return run_ids

    def latest_complete_run(self, queue: str) -> sqlite3.Row | None:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE queue = ? AND status = ? "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (queue, COMPLETE),
            ).fetchone()

    # --------------------------------------------------------------- stage

    def stage_rows(self, run_id: int, rows: Sequence[RoughcastRow]) -> int:
        """把一页抓到的行落进 stage。

        采集期数据唯一的出口(4.2 规则 1)。**全部**行都落,包括装修为空和非毛坯的
        行——§七.8 要求它们「计数并落库,不得静默丢弃」。发布时再按
        `fitment_status` 筛出被排序集 P。
        """
        if not rows:
            return 0
        seen_at = self._now()
        payload = [
            (run_id, row.listing_id, content_hash(row), row.fitment_status,
             json.dumps(_row_payload(row), ensure_ascii=False, sort_keys=True), seen_at)
            for row in rows
        ]
        with self.database.connect() as db:
            db.executemany(
                "INSERT INTO roughcast_crawl_stage "
                "(run_id, listing_id, content_hash, fitment_status, payload_json, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                # 同一 run 内重复出现同一 listing_id 是上游翻页抖动的正常结果
                # (总数变化导致跨页重排),取后见到的那次,不算错误。
                "ON CONFLICT(run_id, listing_id) DO UPDATE SET "
                "content_hash = excluded.content_hash, "
                "fitment_status = excluded.fitment_status, "
                "payload_json = excluded.payload_json, seen_at = excluded.seen_at",
                payload,
            )
            counts = _stage_counts(db, run_id)
            db.execute(
                "UPDATE roughcast_crawl_runs SET items_seen = ?, unknown_fitment_count = ?, "
                "non_roughcast_count = ? WHERE id = ?",
                (counts["items_seen"], counts["unknown"], counts["non_roughcast"], run_id),
            )
        return len(payload)

    def stage_count(self, run_id: int) -> int:
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_stage WHERE run_id = ?", (run_id,)
            ).fetchone()[0])

    # ------------------------------------------------------------- publish

    def complete_run(self, run_id: int) -> dict[str, int]:
        """把 run 判 COMPLETE 并发布——**全部动作在一个事务里**。

        任何一步抛异常,整个事务回滚:`listing_current` 保持原样、没有半更新、
        run 也不会停在 COMPLETE。这正是 V1 数据损坏 bug 的修法。
        """
        with self.database.connect() as db:
            run = db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RunStateError(f"run {run_id} 不存在")
            if run["status"] != RUNNING:
                raise RunStateError(f"run {run_id} 状态是 {run['status']},不是 {RUNNING}")

            # 4.2:只有 pages_done 与 pages_expected 一致才允许判 COMPLETE。
            expected, done = run["pages_expected"], run["pages_done"]
            if expected is None or done != expected:
                raise RunStateError(
                    f"run {run_id} 页数不一致:pages_done={done} pages_expected={expected}"
                )

            now = self._now()
            targets = db.execute(
                "SELECT payload_json FROM roughcast_crawl_stage "
                "WHERE run_id = ? AND fitment_status = ? ORDER BY listing_id",
                (run_id, TARGET_FITMENT),
            ).fetchall()
            rows = [RoughcastRow(**json.loads(row["payload_json"])) for row in targets]

            snapshots_inserted = self._write_change_points(db, run_id, rows, now)
            self._refresh_current(db, run_id, rows, now)
            deactivated = db.execute(
                "UPDATE roughcast_listing_current SET is_active = 0 "
                "WHERE is_active = 1 AND last_seen_run_id != ?",
                (run_id,),
            ).rowcount
            self._upsert_communities(db, rows, now)

            db.execute(
                "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ? WHERE id = ?",
                (COMPLETE, now, run_id),
            )
        return {
            "published": len(rows),
            "snapshots_inserted": snapshots_inserted,
            "deactivated": deactivated,
        }

    def _write_change_points(self, db: sqlite3.Connection, run_id: int,
                             rows: Iterable[RoughcastRow], now: str) -> int:
        """4.5 的变更点写入:变了就 INSERT 新行,没变只前移 last_confirmed_*。"""
        inserted = 0
        for row in rows:
            digest = content_hash(row)
            prev = db.execute(
                "SELECT id, content_hash FROM roughcast_listing_snapshot "
                "WHERE listing_id = ? ORDER BY captured_at DESC, id DESC LIMIT 1",
                (row.listing_id,),
            ).fetchone()
            if prev is not None and prev["content_hash"] == digest:
                db.execute(
                    "UPDATE roughcast_listing_snapshot SET last_confirmed_at = ?, "
                    "last_confirmed_run_id = ? WHERE id = ?",
                    (now, run_id, prev["id"]),
                )
                continue
            db.execute(
                "INSERT INTO roughcast_listing_snapshot "
                "(listing_id, captured_at, captured_run_id, last_confirmed_at, "
                f" last_confirmed_run_id, content_hash, {_BUSINESS_SQL}) "
                f"VALUES (?, ?, ?, ?, ?, ?, {_BUSINESS_PLACEHOLDERS})",
                (row.listing_id, now, run_id, now, run_id, digest, *business_values(row)),
            )
            inserted += 1
        return inserted

    def _refresh_current(self, db: sqlite3.Connection, run_id: int,
                         rows: Iterable[RoughcastRow], now: str) -> None:
        db.executemany(
            f"INSERT INTO roughcast_listing_current "
            f"(listing_id, {_BUSINESS_SQL}, content_hash, first_seen_at, last_seen_at, "
            f" last_seen_run_id, is_active) "
            f"VALUES (?, {_BUSINESS_PLACEHOLDERS}, ?, ?, ?, ?, 1) "
            f"ON CONFLICT(listing_id) DO UPDATE SET {_BUSINESS_EXCLUDED}, "
            # first_seen_at 故意不在更新列表里:它是「首次见到」的下界,只能保留旧值。
            f"content_hash = excluded.content_hash, last_seen_at = excluded.last_seen_at, "
            f"last_seen_run_id = excluded.last_seen_run_id, is_active = 1",
            [
                (row.listing_id, *business_values(row), content_hash(row), now, now, run_id)
                for row in rows
            ],
        )

    def _upsert_communities(self, db: sqlite3.Connection, rows: Iterable[RoughcastRow],
                            now: str) -> None:
        seen: dict[str, dict[str, object]] = {}
        for row in rows:
            key = row.community_id
            if not key:
                continue
            entry = seen.setdefault(key, {
                "name": row.community_name,
                "resblock_id": row.resblock_id,
                "bizcircle": row.bizcircle,
                "count": 0,
            })
            entry["count"] = int(entry["count"]) + 1
        if seen:
            db.executemany(
                "INSERT INTO roughcast_communities "
                "(id, name, resblock_id, bizcircle, roughcast_count, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                # first_seen_at 与队列 B 的 reference_*/refresh_* 列都不在更新列表里。
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "resblock_id = excluded.resblock_id, bizcircle = excluded.bizcircle, "
                "roughcast_count = excluded.roughcast_count",
                [
                    (key, entry["name"], entry["resblock_id"], entry["bizcircle"],
                     entry["count"], now)
                    for key, entry in seen.items()
                ],
            )
        # 本轮没出现的小区套数归零。留着上一轮的计数会让队列 B 的 P2 优先级
        # (按清水房套数排)拿着过期数字排序。
        if seen:
            placeholders = ", ".join("?" for _ in seen)
            db.execute(
                f"UPDATE roughcast_communities SET roughcast_count = 0 "
                f"WHERE roughcast_count != 0 AND id NOT IN ({placeholders})",
                tuple(seen),
            )
        else:
            db.execute(
                "UPDATE roughcast_communities SET roughcast_count = 0 WHERE roughcast_count != 0"
            )

    # ----------------------------------------------------------- crawl log

    def log_request(self, *, run_id: int | None, queue: str, target: str, status: str,
                    http_status: int | None = None, note: str | None = None) -> int:
        with self.database.connect() as db:
            cursor = db.execute(
                "INSERT INTO roughcast_crawl_log "
                "(run_id, queue, target, requested_at, status, http_status, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, queue, target, self._now(), status, http_status, note),
            )
            log_id = int(cursor.lastrowid)
        return log_id

    def update_request_log(self, log_id: int, *, status: str, http_status: int | None = None,
                           note: str | None = None) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_crawl_log SET status = ?, http_status = ?, note = ? WHERE id = ?",
                (status, http_status, note, log_id),
            )

    def count_requests_since(self, since: datetime) -> int:
        """当天已花预算。以 crawl_log 为准而不是内存计数器——见第三章。"""
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_log WHERE requested_at >= ?",
                (since.isoformat(),),
            ).fetchone()[0])

    def sync_run_request_count(self, run_id: int) -> int:
        with self.database.connect() as db:
            count = int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_log WHERE run_id = ?", (run_id,)
            ).fetchone()[0])
            db.execute(
                "UPDATE roughcast_crawl_runs SET request_count = ? WHERE id = ?", (count, run_id)
            )
        return count

    # ---------------------------------------------------------------- read

    def get_current(self, listing_id: str) -> sqlite3.Row | None:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_listing_current WHERE listing_id = ?", (listing_id,)
            ).fetchone()

    def active_count(self) -> int:
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_listing_current WHERE is_active = 1"
            ).fetchone()[0])

    def snapshots_for(self, listing_id: str) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_listing_snapshot WHERE listing_id = ? "
                "ORDER BY captured_at, id",
                (listing_id,),
            ).fetchall()

    def community(self, community_id: str) -> sqlite3.Row | None:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_communities WHERE id = ?", (community_id,)
            ).fetchone()

    # ------------------------------------------------------- 观察期只读盘点
    # 下面几个方法只服务 `roughcast_status`(第 1 期出口条件的日常盘点),
    # 一个字节都不写。

    def recent_runs(self, *, limit: int, queue: str | None = None) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            if queue is None:
                return db.execute(
                    "SELECT * FROM roughcast_crawl_runs "
                    "ORDER BY started_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE queue = ? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (queue, limit),
            ).fetchall()

    def staged_target_counts(self, run_ids: Sequence[int]) -> dict[int, int]:
        """每轮 stage 里的毛坯行数。COMPLETE 轮的这个数就是它发布了多少套。

        `CrawlOutcome.published` 只活在进程内存里,事后盘点只能这样数回来。
        """
        return self._counts_by_run(
            "SELECT run_id AS rid, COUNT(*) AS n FROM roughcast_crawl_stage "
            "WHERE run_id IN ({placeholders}) AND fitment_status = ? GROUP BY run_id",
            run_ids,
            (TARGET_FITMENT,),
        )

    def snapshot_insert_counts(self, run_ids: Sequence[int]) -> dict[int, int]:
        """每轮新写入的变更点行数(4.5)。

        非 COMPLETE 的 run 在这里**必须**是 0——快照只在 `complete_run()` 的事务里写。
        不为 0 就是 4.2 说的那类「最难通过抽查发现」的越界发布。
        """
        return self._counts_by_run(
            "SELECT captured_run_id AS rid, COUNT(*) AS n FROM roughcast_listing_snapshot "
            "WHERE captured_run_id IN ({placeholders}) GROUP BY captured_run_id",
            run_ids,
        )

    def _counts_by_run(self, sql: str, run_ids: Sequence[int],
                       extra: tuple[object, ...] = ()) -> dict[int, int]:
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        with self.database.connect() as db:
            rows = db.execute(
                sql.format(placeholders=placeholders), (*run_ids, *extra)
            ).fetchall()
        return {int(row["rid"]): int(row["n"]) for row in rows}

    def requests_since(self, since: datetime) -> list[sqlite3.Row]:
        """时间升序的原始 `crawl_log` 行。节奏与窗口的合规性只能从这里看出来。"""
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_log WHERE requested_at >= ? "
                "ORDER BY requested_at, id",
                (since.isoformat(),),
            ).fetchall()

    def runs_since(self, since: datetime) -> list[sqlite3.Row]:
        """按固定时间窗取 run。出口条件问的是「连续 3 天」,答案不该随 `--status N` 变。"""
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE started_at >= ? "
                "ORDER BY started_at, id",
                (since.isoformat(),),
            ).fetchall()

    def status_totals(self) -> dict[str, int]:
        with self.database.connect() as db:
            return {
                "active": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_current "
                                     "WHERE is_active = 1"),
                "inactive": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_current "
                                       "WHERE is_active = 0"),
                "snapshots": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_snapshot"),
                "communities": _scalar(db, "SELECT COUNT(*) FROM roughcast_communities"),
            }


def _row_payload(row: RoughcastRow) -> dict[str, object]:
    return {"listing_id": row.listing_id,
            **{name: getattr(row, name) for name in BUSINESS_FIELDS}}


def _scalar(db: sqlite3.Connection, sql: str) -> int:
    return int(db.execute(sql).fetchone()[0])


def _stage_counts(db: sqlite3.Connection, run_id: int) -> dict[str, int]:
    row = db.execute(
        "SELECT COUNT(*) AS total, "
        # 装修未知 = NULL 或空串。§七.8:布尔会把「简装/精装/未知」压成同一个值,
        # 所以这里按原值分三类计数,不做二分。
        "SUM(CASE WHEN fitment_status IS NULL OR fitment_status = '' THEN 1 ELSE 0 END) AS unknown, "
        "SUM(CASE WHEN fitment_status IS NOT NULL AND fitment_status NOT IN ('', ?) "
        "         THEN 1 ELSE 0 END) AS non_roughcast "
        "FROM roughcast_crawl_stage WHERE run_id = ?",
        (TARGET_FITMENT, run_id),
    ).fetchone()
    return {
        "items_seen": int(row["total"] or 0),
        "unknown": int(row["unknown"] or 0),
        "non_roughcast": int(row["non_roughcast"] or 0),
    }
