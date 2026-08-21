"""清水房采集的仓储层（`docs/roughcast-quality-ranking.md` §4.2 / §4.5）。

这一层持有 run 状态机的**事务边界**。发布（写 `listing_current` /
`listing_snapshot` / `roughcast_communities`）的终态是 `COMPLETE` 或
`PARTIAL`——V2.5 起 PARTIAL 加入状态机,允许 11 档切分中部分档失败的轮
只发布成功档,失败档的 stage 行作废。`ABORTED` / `FAILED` 的 run 一个字节
都碰不到 `listing_*` 表（数据止步于 `crawl_stage`）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Callable, Iterable, Mapping, Sequence

from app.domain.roughcast import (
    BUSINESS_FIELDS,
    SCORE_SOURCES,
    SCORE_SOURCE_FALLBACK,
    SCORE_SOURCE_NEIGHBOR,
    SCORE_SOURCE_SELF,
    RoughcastRow,
    business_values,
    content_hash,
)
from app.infrastructure.database import Database

logger = logging.getLogger(__name__)

RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"          # V2.5:11 档切分中部分档成功
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

    def start_run(self, queue: str, *,
                  planned_buckets: Sequence[tuple[int | None, int | None]] | None = None
                  ) -> int:
        """开一轮。

        `planned_buckets` 是 11 档切分的 (lo, hi) 序列;`None` 走旧路径
        (单一查询,无档切分),存为 `[]`。档的 JSON 形式是 `[[lo, hi], ...]`,
        lo/hi 为 None 时序列化成 `null`——便于事后回溯和 `--status` 展示。
        """
        payload = json.dumps(
            [[lo, hi] for lo, hi in (planned_buckets or ())],
            ensure_ascii=False,
        )
        with self.database.connect() as db:
            cursor = db.execute(
                "INSERT INTO roughcast_crawl_runs (queue, status, started_at, planned_buckets) "
                "VALUES (?, ?, ?, ?)",
                (queue, RUNNING, self._now(), payload),
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

    def latest_published_run(self, queue: str) -> sqlite3.Row | None:
        """最新**已发布**的 run。COMPLETE 与 PARTIAL 都算发布(4.2 规则 1)。"""
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs "
                "WHERE queue = ? AND status IN (?, ?) "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (queue, COMPLETE, PARTIAL),
            ).fetchone()

    def record_bucket_outcomes(self, run_id: int,
                               outcomes: Mapping[str, str]) -> None:
        """`bucket_outcomes` 字段由 caller(队列编排)写入。键是 bucket 标签
        (如 `0:800`),值是 `success` / `skipped_over_cap` / `bucket_failed`。

        与 `abort_reason` 共存不冲突:PARTIAL 落 `status=PARTIAL`,
        `abort_reason` 写 `'partial:<bucket>=<reason>'` 概要,详细结构在
        `bucket_outcomes` 里——便于 `--status` 一次性展示。

        终态在 `complete_run()` 里会被「完整 11 档映射」覆写一次(每档
        都得有一行,不能漏);这里只是中途进度。
        """
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_crawl_runs SET bucket_outcomes = ? WHERE id = ?",
                (json.dumps(dict(outcomes), ensure_ascii=False, sort_keys=True), run_id),
            )

    # --------------------------------------------------------------- stage

    def stage_rows(self, run_id: int, rows: Sequence[RoughcastRow], *,
                  bucket: str = "0:+inf") -> tuple[int, int]:
        """把一页抓到的行落进 stage。

        采集期数据唯一的出口(4.2 规则 1)。**全部**行都落,包括装修为空和非毛坯的
        行——§七.8 要求它们「计数并落库,不得静默丢弃」。发布时再按
        `fitment_status` 筛出被排序集 P。

        V2.5:`bucket` 标记本批行来自哪一档;PARTIAL 时只发布成功档的行。
        默认 `'0:+inf'` 兼容旧路径(单一查询时代)。

        返回 `(inserted, returned)`:inserted 是实际新增行数(去重后),returned
        是上游本批返回的原始行数。这两数在调用方用来回填 `crawl_log.rows_new /
        rows_returned`,让「第 21 页起新增 0 行」这种深分页病灶立刻刺眼。
        """
        returned = len(rows)
        if not rows:
            return 0, 0
        seen_at = self._now()
        payload = [
            (run_id, row.listing_id, content_hash(row), row.fitment_status,
             json.dumps(_row_payload(row), ensure_ascii=False, sort_keys=True),
             seen_at, bucket)
            for row in rows
        ]
        with self.database.connect() as db:
            before_count = int(db.execute(
                "SELECT COUNT(DISTINCT listing_id) FROM roughcast_crawl_stage WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0])
            db.executemany(
                "INSERT INTO roughcast_crawl_stage "
                "(run_id, listing_id, content_hash, fitment_status, payload_json, seen_at, "
                " bucket) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                # 同一 run 内重复出现同一 listing_id 是上游翻页抖动的正常结果
                # (总数变化导致跨页重排),取后见到的那次,不算错误。
                "ON CONFLICT(run_id, listing_id) DO UPDATE SET "
                "content_hash = excluded.content_hash, "
                "fitment_status = excluded.fitment_status, "
                "payload_json = excluded.payload_json, seen_at = excluded.seen_at, "
                "bucket = excluded.bucket",
                payload,
            )
            inserted = _count_new_rows(db, run_id, before_count)
            counts = _stage_counts(db, run_id)
            db.execute(
                "UPDATE roughcast_crawl_runs SET items_seen = ?, unknown_fitment_count = ?, "
                "non_roughcast_count = ? WHERE id = ?",
                (counts["items_seen"], counts["unknown"], counts["non_roughcast"], run_id),
            )
        return inserted, returned

    def stage_count(self, run_id: int) -> int:
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_stage WHERE run_id = ?", (run_id,)
            ).fetchone()[0])

    # ------------------------------------------------------------- publish

    def complete_run(
        self, run_id: int, *,
        bucket_outcomes: Mapping[str, str] | None = None,
        bucket_details: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, int]:
        """把 run 收尾并发布——**全部动作在一个事务里**。

        V2.5 行为:接受 `bucket_outcomes` 形如 `{"0:800": "success", "1200:1500":
        "skipped_over_cap", ...}`。

        终态判定:
        - 空 / 全 success → `COMPLETE`,发布全部 stage 行
        - 部分 success 部分失败 → `PARTIAL`,**只发布成功档的 stage 行**,
          失败档的行作废(留在 stage 但不进 listing_*)
        - 全失败 → 抛 `RunStateError`,由调用方走 ABORTED 收尾

        任何一步抛异常,整个事务回滚:`listing_current` 保持原样、没有半更新、
        run 也不会停在 COMPLETE / PARTIAL。这正是 V1 数据损坏 bug 的修法。
        """
        if bucket_outcomes is not None:
            if not bucket_outcomes:
                raise RunStateError(
                    f"run {run_id}:bucket_outcomes 为空,无法确定终态"
                )
            statuses = set(bucket_outcomes.values())
            success_buckets = [b for b, s in bucket_outcomes.items() if s == "success"]
            failed_buckets = [b for b, s in bucket_outcomes.items() if s != "success"]
            if not success_buckets:
                raise RunStateError(
                    f"run {run_id}:0 档成功,应走 ABORTED 而非 complete_run"
                )
            terminal_status = COMPLETE if not failed_buckets else PARTIAL
            allowed_buckets = tuple(success_buckets)
        else:
            terminal_status = COMPLETE
            allowed_buckets = None                    # 旧路径:全发

        with self.database.connect() as db:
            run = db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RunStateError(f"run {run_id} 不存在")
            if run["status"] != RUNNING:
                raise RunStateError(f"run {run_id} 状态是 {run['status']},不是 {RUNNING}")

            # 4.2:只有 pages_done 与 pages_expected 一致才允许判终态。
            # PARTIAL 也走这条:成功档必须全翻完。
            expected, done = run["pages_expected"], run["pages_done"]
            if expected is None or done != expected:
                raise RunStateError(
                    f"run {run_id} 页数不一致:pages_done={done} pages_expected={expected}"
                )

            now = self._now()
            sql = (
                "SELECT payload_json FROM roughcast_crawl_stage "
                "WHERE run_id = ? AND fitment_status = ?"
            )
            params: list[object] = [run_id, TARGET_FITMENT]
            if allowed_buckets is not None:
                placeholders = ", ".join("?" for _ in allowed_buckets)
                sql += f" AND bucket IN ({placeholders})"
                params.extend(allowed_buckets)
            sql += " ORDER BY listing_id"
            targets = db.execute(sql, params).fetchall()
            rows = [RoughcastRow(**json.loads(row["payload_json"])) for row in targets]

            snapshots_inserted = self._write_change_points(db, run_id, rows, now)
            self._refresh_current(db, run_id, rows, now)
            deactivated = db.execute(
                "UPDATE roughcast_listing_current SET is_active = 0 "
                "WHERE is_active = 1 AND last_seen_run_id != ?",
                (run_id,),
            ).rowcount
            self._upsert_communities(db, rows, now)
            score_source_counts = self._assign_score_sources(db, run_id)

            abort_reason = None
            if terminal_status == PARTIAL:
                failed_summary = ", ".join(
                    f"{b}={bucket_outcomes[b]}" for b in failed_buckets
                )
                abort_reason = f"partial:{failed_summary}"

            # `bucket_outcomes` 列只存简单的 `{"0:800": "success", ...}` 状态映射,
            # 方便 `--status` 一眼看清每档的判定。详细结构(`pages`/`total`/
            # `reason`)在 PARTIAL 时拼进 `abort_reason`(失败档那行),其他
            # 终态不需要持久化。
            db.execute(
                "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ?, "
                "abort_reason = ?, bucket_outcomes = ? WHERE id = ?",
                (terminal_status, now, abort_reason,
                 json.dumps(dict(bucket_outcomes or {}), ensure_ascii=False, sort_keys=True),
                 run_id),
            )
        return {
            "published": len(rows),
            "snapshots_inserted": snapshots_inserted,
            "deactivated": deactivated,
            "score_source_counts": score_source_counts,
        }

    def _assign_score_sources(self, db: sqlite3.Connection, run_id: int) -> dict[str, int]:
        """§4.6:给本轮刷新的 listing_current 行打 score_source 三值标记。

        判定顺序(用 CASE 而非多次 UPDATE 拼接,事务里只一次写):
        1. self:本 listing 的 `community_id` 命中 `roughcast_communities.id`
        2. neighbor:第 1 条不命中,但同 `bizcircle` 至少 1 个其他小区命中
        3. fallback:以上都不命中——通常是小小区或上游没给 bizcircle

        写完后再 GROUP BY 数一遍三值计数,给 `--status` 和日志用。
        """
        db.execute(
            "UPDATE roughcast_listing_current SET score_source = CASE "
            "  WHEN community_id IN (SELECT id FROM roughcast_communities "
            "                        WHERE id IS NOT NULL) THEN ? "
            "  WHEN bizcircle IN (SELECT bizcircle FROM roughcast_communities "
            "                     WHERE bizcircle IS NOT NULL) THEN ? "
            "  ELSE ? "
            "END "
            "WHERE last_seen_run_id = ?",
            (SCORE_SOURCE_SELF, SCORE_SOURCE_NEIGHBOR, SCORE_SOURCE_FALLBACK, run_id),
        )
        counts = db.execute(
            "SELECT score_source, COUNT(*) AS n FROM roughcast_listing_current "
            "WHERE last_seen_run_id = ? GROUP BY score_source",
            (run_id,),
        ).fetchall()
        result: dict[str, int] = {src: 0 for src in SCORE_SOURCES}
        for row in counts:
            result[str(row["score_source"])] = int(row["n"])
        return result

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

    def update_request_log_counters(self, log_id: int, *, rows_returned: int,
                                     rows_new: int) -> None:
        """V2.5:回填 `crawl_log.rows_returned / rows_new`。

        `crawl_log` 一行 = 一次上游请求,但 stage 落库发生在请求返回之后,
        两者时间不同步。把这两个数补上,「rows_new=0 且 rows_returned>0」就
        能直接查出来——这是深分页硬顶的签名,run 1 漏 70% 就是这病灶。
        """
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_crawl_log SET rows_returned = ?, rows_new = ? "
                "WHERE id = ?",
                (rows_returned, rows_new, log_id),
            )

    def count_rows_with_zero_new(self, since: datetime) -> int:
        """诊断:近一段时间内「返回了行但没新增」的请求数。健康值应接近 0。
        """
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_log "
                "WHERE requested_at >= ? AND status = 'ok' "
                "AND rows_returned > 0 AND rows_new = 0",
                (since.isoformat(),),
            ).fetchone()[0])

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
                # V2.5:score_source 三值 + community_lookup_status 二值
                # (`§4.6`)。这些是当前态的分布,与 run 无关;`--status` 一次查清。
                "score_self": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_current "
                                          "WHERE is_active = 1 AND score_source = 'self'"),
                "score_neighbor": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_current "
                                              "WHERE is_active = 1 AND score_source = 'neighbor'"),
                "score_fallback": _scalar(db, "SELECT COUNT(*) FROM roughcast_listing_current "
                                              "WHERE is_active = 1 AND score_source = 'fallback'"),
                "community_not_found": _scalar(
                    db, "SELECT COUNT(*) FROM roughcast_listing_current "
                        "WHERE is_active = 1 AND community_lookup_status = 'not_found'"
                ),
            }

    def bucket_outcomes_for(self, run_id: int) -> dict[str, str] | None:
        """V2.5:从 `roughcast_crawl_runs.bucket_outcomes`(JSON 列)反序列化。

        COMPLETE / PARTIAL 才有值;RUNNING / ABORTED / FAILED 落 None。
        """
        import json
        with self.database.connect() as db:
            row = db.execute(
                "SELECT bucket_outcomes FROM roughcast_crawl_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None or not row["bucket_outcomes"]:
            return None
        try:
            return {str(k): str(v) for k, v in json.loads(row["bucket_outcomes"]).items()}
        except (TypeError, ValueError):
            return None

    def coverage_stats_for(self, run_id: int) -> dict[str, int]:
        """V2.5:`crawl_log.rows_returned / rows_new` 的覆盖度审计。

        返回 `{requests, rows_returned_total, rows_new_total, dropped_pages}`:
        - `dropped_pages` = `rows_new=0 AND rows_returned>0` 的请求数,
          这是深分页硬顶的签名([[roughcast-deep-paging-cap]])。
        """
        with self.database.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS n, "
                "       COALESCE(SUM(rows_returned), 0) AS rr, "
                "       COALESCE(SUM(rows_new), 0) AS rn "
                "FROM roughcast_crawl_log WHERE run_id = ? AND status = 'ok'",
                (run_id,),
            ).fetchone()
            dropped = int(db.execute(
                "SELECT COUNT(*) FROM roughcast_crawl_log "
                "WHERE run_id = ? AND status = 'ok' "
                "AND rows_returned > 0 AND rows_new = 0",
                (run_id,),
            ).fetchone()[0])
        return {
            "requests": int(row["n"]),
            "rows_returned_total": int(row["rr"]),
            "rows_new_total": int(row["rn"]),
            "dropped_pages": int(dropped),
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


def _count_new_rows(db: sqlite3.Connection, run_id: int, before_count: int) -> int:
    """`stage_rows` 后调用:本批新增的(去重)行数。

    思路:在 `stage_rows` 落库前记一次 `(run_id, listing_id) 去重计数`,
    落库后再记一次,差即为本批真正新增——`ON CONFLICT DO UPDATE` 走的也是
    rowcount=1,直接靠 cursor 算不出来。`COUNT(DISTINCT listing_id)` 把
    翻页抖动去重后的版本作为分母,语义与 `_stage_counts` 一致。
    """
    after_count = int(db.execute(
        "SELECT COUNT(DISTINCT listing_id) FROM roughcast_crawl_stage WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0])
    return max(after_count - before_count, 0)
