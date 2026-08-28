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

from app.infrastructure.district_catalog import DistrictCatalog, classify_bizcircle
from app.domain.roughcast import (
    BUSINESS_FIELDS,
    REFERENCE_FITMENTS,
    SCORE_SOURCES,
    SCORE_SOURCE_FALLBACK,
    SCORE_SOURCE_NEIGHBOR,
    SCORE_SOURCE_SELF,
    RoughcastRow,
    business_values,
    content_hash,
    is_roughcast_status,
    unit_rent_of,
)
from app.infrastructure.database import Database

logger = logging.getLogger(__name__)

RUNNING = "RUNNING"
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"          # V2.5:11 档切分中部分档成功
ABORTED = "ABORTED"
FAILED = "FAILED"

TARGET_FITMENT = "002"  # 毛坯。'001' 简装 / '003' 精装 属参考集 R,不进被排序集 P
QUEUE_B = "B"
REFERENCE_REFRESH_DAYS = 15  # 队列 B 半月轮转(约束 6)
COMMUNITY_SUCCESS = "success"
COMMUNITY_FAILED = "community_failed"
COMMUNITY_OVER_CAP = "skipped_over_cap"

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

    def runs_for_queue_since(self, queue: str, since: datetime) -> list[sqlite3.Row]:
        """给定队列在本日起跑过哪些 run——`since` 传 UTC `datetime`。

        日 loop 的「今天已经采过 A 没」就靠这条查;不走 `_last_run_day`
        内存态,这样**进程重启也安全**:同一日内构造一个全新的
        `RoughcastDailyLoop` 实例,SQLite 仍记得上一轮留下的行,循环
        不会偷偷起第二轮。任何状态都算(RUNNING / COMPLETE / PARTIAL /
        ABORTED / FAILED)——只有「在那天起过」这一条事实需要被记住。
        """
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_crawl_runs "
                "WHERE queue = ? AND started_at >= ? "
                "ORDER BY started_at ASC, id ASC",
                (queue, since.isoformat()),
            ).fetchall()

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
            #
            # V2.5 修:`upstream_total` 这里覆盖写成 11 档 `totalCount` 之和,
            # 才是 V2.4 单查询时代「全市毛坯总数」的语义对应。原本被每页
            # `record_progress(upstream_total=...)` 反复覆写,最终值只是
            # 「最后一档的 totalCount」,报"3290→916 极差 259%"是错的。
            # V2.4 单查询路径不传 `bucket_details`,保持原样不动。
            sum_total: int | None = None
            if bucket_details:
                bucket_totals = [
                    int(d["total"])
                    for d in bucket_details.values()
                    if isinstance(d, Mapping) and d.get("total") is not None
                ]
                if bucket_totals:
                    sum_total = sum(bucket_totals)
            db.execute(
                "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ?, "
                "abort_reason = ?, bucket_outcomes = ?, upstream_total = ? "
                "WHERE id = ?",
                (terminal_status, now, abort_reason,
                 json.dumps(dict(bucket_outcomes or {}), ensure_ascii=False, sort_keys=True),
                 sum_total if sum_total is not None else run["upstream_total"],
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
            self._refresh_community_districts(
                db,
                [
                    (key, str(entry["bizcircle"]) if entry["bizcircle"] else None)
                    for key, entry in seen.items()
                ],
                now,
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

    def replace_bizcircle_district_catalog(
        self, catalog: DistrictCatalog, *, captured_at: str
    ) -> None:
        with self.database.connect() as db:
            db.execute("DELETE FROM roughcast_bizcircle_district")
            db.executemany(
                "INSERT INTO roughcast_bizcircle_district "
                "(bizcircle, district, captured_at) VALUES (?, ?, ?)",
                [(bizcircle, district, captured_at) for bizcircle, district in catalog.pairs()],
            )

    def list_communities_for_district(self) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            return db.execute(
                "SELECT id, name, bizcircle, district_source "
                "FROM roughcast_communities ORDER BY id"
            ).fetchall()

    def list_communities_pending_baidu(self) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            return db.execute(
                "SELECT id, name, bizcircle FROM roughcast_communities "
                "WHERE district_source IS NULL OR district_source NOT IN ('baidu', 'beike_map') "
                "ORDER BY name, id"
            ).fetchall()

    def count_baidu_marked_communities(self) -> int:
        with self.database.connect() as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM roughcast_communities "
                "WHERE district_source IN ('baidu', 'beike_map')"
            ).fetchone()[0])

    def apply_baidu_community_mark(
        self, community_id: str, hit, *, assigned_at: str, source: str = "baidu"
    ) -> None:
        from app.infrastructure.district_catalog import STATUS_UNIQUE

        district = str(hit.district)
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_communities SET district = ?, district_status = ?, "
                "districts_json = ?, district_assigned_at = ?, district_source = ?, "
                "latitude = ?, longitude = ?, baidu_uid = ?, baidu_poi_name = ? "
                "WHERE id = ?",
                (
                    district, STATUS_UNIQUE, json.dumps([district], ensure_ascii=False),
                    assigned_at, source, hit.latitude, hit.longitude, hit.uid, hit.name,
                    community_id,
                ),
            )
            db.execute(
                "DELETE FROM roughcast_community_district WHERE community_id = ?",
                (community_id,),
            )
            db.execute(
                "INSERT INTO roughcast_community_district (community_id, district) "
                "VALUES (?, ?)",
                (community_id, district),
            )

    def apply_community_district_assignments(
        self,
        assignments: Sequence[tuple[str, str | None, tuple[str, ...], str]],
        *,
        assigned_at: str,
    ) -> None:
        with self.database.connect() as db:
            self._write_community_districts(db, assignments, assigned_at)

    def load_district_catalog(self) -> DistrictCatalog:
        with self.database.connect() as db:
            return self._load_district_catalog(db)

    def _refresh_community_districts(
        self,
        db: sqlite3.Connection,
        communities: Sequence[tuple[str, str | None]],
        now: str,
    ) -> None:
        catalog = self._load_district_catalog(db)
        if not catalog.bizcircle_to_districts:
            return
        ids = [community_id for community_id, _biz in communities]
        placeholders = ", ".join("?" for _ in ids)
        locked = {
            row["id"]
            for row in db.execute(
                f"SELECT id FROM roughcast_communities "
                f"WHERE district_source IN ('baidu', 'beike_map') AND id IN ({placeholders})",
                ids,
            )
        }
        assignments = [
            (community_id, *classify_bizcircle(catalog, bizcircle))
            for community_id, bizcircle in communities
            if community_id not in locked
        ]
        self._write_community_districts(db, assignments, now)

    def _load_district_catalog(self, db: sqlite3.Connection) -> DistrictCatalog:
        try:
            rows = db.execute(
                "SELECT bizcircle, district FROM roughcast_bizcircle_district "
                "ORDER BY district, bizcircle"
            ).fetchall()
        except sqlite3.OperationalError:
            return DistrictCatalog(districts=(), bizcircle_to_districts={})
        return DistrictCatalog.from_pairs(
            [(row["bizcircle"], row["district"]) for row in rows]
        )

    def _write_community_districts(
        self,
        db: sqlite3.Connection,
        assignments: Sequence[tuple[str, str | None, tuple[str, ...], str]],
        now: str,
    ) -> None:
        if not assignments:
            return
        db.executemany(
            "UPDATE roughcast_communities SET district = ?, district_status = ?, "
            "districts_json = ?, district_assigned_at = ? WHERE id = ?",
            [
                (
                    district,
                    status,
                    json.dumps(list(names), ensure_ascii=False),
                    now,
                    community_id,
                )
                for community_id, district, names, status in assignments
            ],
        )
        ids = [community_id for community_id, _district, _names, _status in assignments]
        placeholders = ", ".join("?" for _ in ids)
        db.execute(
            f"DELETE FROM roughcast_community_district WHERE community_id IN ({placeholders})",
            ids,
        )
        pairs = [
            (community_id, name)
            for community_id, _district, names, _status in assignments
            for name in names
        ]
        if pairs:
            db.executemany(
                "INSERT INTO roughcast_community_district (community_id, district) "
                "VALUES (?, ?)",
                pairs,
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

    def list_queue_b_targets(self, *, limit: int | None = None,
                             unreferenced_only: bool = False) -> list[dict[str, object]]:
        """队列 B 的小区名单。优先级:P0 从未有基准 → P1 上次失败 → P2 清水房多 → P3 最旧。

        `unreferenced_only=True` 只取还没有 `reference_run_id` 的小区——全量第一次
        与中断后续跑都走这条,已经成功发布过的小区不会被再买一轮请求。
        没有 `resblock_id` 的占位小区搜不了,直接排除。
        """
        where = ["resblock_id IS NOT NULL", "TRIM(resblock_id) != ''"]
        if unreferenced_only:
            where.append("reference_run_id IS NULL")
        sql = (
            "SELECT id, name, resblock_id, bizcircle, roughcast_count, "
            "       reference_run_id, refreshed_at, refresh_fail_count "
            "FROM roughcast_communities "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY "
            "  CASE WHEN reference_run_id IS NULL THEN 0 ELSE 1 END, "
            "  CASE WHEN refresh_fail_count > 0 THEN 0 ELSE 1 END, "
            "  roughcast_count DESC, "
            "  CASE WHEN refreshed_at IS NULL THEN 0 ELSE 1 END, "
            "  refreshed_at ASC, id"
        )
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (int(limit),)
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def queue_b_inventory(self) -> dict[str, int]:
        with self.database.connect() as db:
            return {
                "communities": _scalar(db, "SELECT COUNT(*) FROM roughcast_communities"),
                "searchable": _scalar(
                    db,
                    "SELECT COUNT(*) FROM roughcast_communities "
                    "WHERE resblock_id IS NOT NULL AND TRIM(resblock_id) != ''",
                ),
                "unreferenced": _scalar(
                    db,
                    "SELECT COUNT(*) FROM roughcast_communities "
                    "WHERE resblock_id IS NOT NULL AND TRIM(resblock_id) != '' "
                    "AND reference_run_id IS NULL",
                ),
                "referenced": _scalar(
                    db,
                    "SELECT COUNT(*) FROM roughcast_communities "
                    "WHERE reference_run_id IS NOT NULL",
                ),
                "skipped_no_resblock": _scalar(
                    db,
                    "SELECT COUNT(*) FROM roughcast_communities "
                    "WHERE resblock_id IS NULL OR TRIM(resblock_id) = ''",
                ),
                "reference_rows": _scalar(
                    db, "SELECT COUNT(*) FROM roughcast_community_reference_snapshot"
                ),
            }

    def mark_community_refresh_failed(self, community_id: str) -> None:
        """小区本轮没能给出完整参考集。指针不动,失败计数 +1,下次 P1 优先补。"""
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_communities "
                "SET refresh_fail_count = refresh_fail_count + 1 WHERE id = ?",
                (community_id,),
            )

    def complete_queue_b_run(
        self, run_id: int, *,
        community_outcomes: Mapping[str, str],
    ) -> dict[str, object]:
        """发布队列 B:把成功小区的 stage 行写入参考快照,并前移 `reference_run_id`。

        **不碰** `listing_current` / `listing_snapshot` / `is_active`——那些是队列 A
        的被排序集 P。ABORTED / FAILED 不得调用本方法。

        `community_outcomes` 键是 `roughcast_communities.id`,值是
        `success` / `community_failed` / `skipped_over_cap`。未出现的小区本轮没采,
        指针与失败计数都不动。
        """
        if not community_outcomes:
            raise RunStateError(f"run {run_id}:community_outcomes 为空,无法确定终态")
        success_ids = [cid for cid, status in community_outcomes.items()
                       if status == COMMUNITY_SUCCESS]
        failed_ids = [cid for cid, status in community_outcomes.items()
                      if status != COMMUNITY_SUCCESS]
        if not success_ids:
            raise RunStateError(
                f"run {run_id}:0 个小区成功,应走 FAILED 而非 complete_queue_b_run"
            )
        terminal_status = COMPLETE if not failed_ids else PARTIAL

        with self.database.connect() as db:
            run = db.execute(
                "SELECT * FROM roughcast_crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise RunStateError(f"run {run_id} 不存在")
            if run["status"] != RUNNING:
                raise RunStateError(f"run {run_id} 状态是 {run['status']},不是 {RUNNING}")
            expected, done = run["pages_expected"], run["pages_done"]
            if expected is None or done != expected:
                raise RunStateError(
                    f"run {run_id} 页数不一致:pages_done={done} pages_expected={expected}"
                )

            now = self._now()
            next_refresh = (self._clock() + timedelta(days=REFERENCE_REFRESH_DAYS)).isoformat()
            inserted = 0
            r_rows = 0
            for community_id in success_ids:
                bucket = _queue_b_bucket(community_id)
                staged = db.execute(
                    "SELECT payload_json FROM roughcast_crawl_stage "
                    "WHERE run_id = ? AND bucket = ?",
                    (run_id, bucket),
                ).fetchall()
                rows = [RoughcastRow(**json.loads(row["payload_json"])) for row in staged]
                inserted += self._write_reference_snapshot(db, run_id, community_id, rows, now)
                r_rows += sum(
                    1 for row in rows
                    if row.fitment_status in REFERENCE_FITMENTS
                )
                db.execute(
                    "UPDATE roughcast_communities "
                    "SET reference_run_id = ?, refreshed_at = ?, next_refresh_at = ?, "
                    "    refresh_fail_count = 0 "
                    "WHERE id = ?",
                    (run_id, now, next_refresh, community_id),
                )
            for community_id in failed_ids:
                db.execute(
                    "UPDATE roughcast_communities "
                    "SET refresh_fail_count = refresh_fail_count + 1 WHERE id = ?",
                    (community_id,),
                )

            score_source_counts = self._assign_score_sources_from_references(db)
            abort_reason = None
            if terminal_status == PARTIAL:
                failed_summary = ", ".join(
                    f"{cid}={community_outcomes[cid]}" for cid in failed_ids
                )
                abort_reason = f"partial:{failed_summary}"
            db.execute(
                "UPDATE roughcast_crawl_runs SET status = ?, finished_at = ?, "
                "abort_reason = ?, bucket_outcomes = ? WHERE id = ?",
                (terminal_status, now, abort_reason,
                 json.dumps(dict(community_outcomes), ensure_ascii=False, sort_keys=True),
                 run_id),
            )
        return {
            "communities_published": len(success_ids),
            "communities_failed": len(failed_ids),
            "reference_rows": inserted,
            "reference_r_rows": r_rows,
            "score_source_counts": score_source_counts,
        }

    def _write_reference_snapshot(
        self, db: sqlite3.Connection, run_id: int, community_id: str,
        rows: Sequence[RoughcastRow], now: str,
    ) -> int:
        """参考快照按批次 append,本 run 内 listing_id 去重。全部行都写,含未知装修。"""
        if not rows:
            return 0
        payload = [
            (
                community_id, run_id, row.listing_id, row.rent_mode,
                row.rooms, row.halls, row.baths, row.area_sqm, row.monthly_rent_yuan,
                unit_rent_of(row.area_sqm, row.monthly_rent_yuan),
                row.orientation, int(is_roughcast_status(row.fitment_status)),
                row.fitment_status, now,
            )
            for row in rows
        ]
        db.executemany(
            "INSERT INTO roughcast_community_reference_snapshot "
            "(community_id, run_id, listing_id, rent_mode, rooms, halls, baths, "
            " area_sqm, monthly_rent_yuan, unit_rent, orientation, is_roughcast, "
            " fitment_status, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, listing_id) DO UPDATE SET "
            "community_id = excluded.community_id, "
            "rent_mode = excluded.rent_mode, rooms = excluded.rooms, "
            "halls = excluded.halls, baths = excluded.baths, "
            "area_sqm = excluded.area_sqm, monthly_rent_yuan = excluded.monthly_rent_yuan, "
            "unit_rent = excluded.unit_rent, orientation = excluded.orientation, "
            "is_roughcast = excluded.is_roughcast, fitment_status = excluded.fitment_status, "
            "captured_at = excluded.captured_at",
            payload,
        )
        return len(payload)

    def _assign_score_sources_from_references(self, db: sqlite3.Connection) -> dict[str, int]:
        """队列 B 发布后:有本小区参考快照 → self;否则同商圈有 → neighbor。"""
        db.execute(
            "UPDATE roughcast_listing_current SET score_source = CASE "
            "  WHEN community_id IN (SELECT id FROM roughcast_communities "
            "                        WHERE reference_run_id IS NOT NULL) THEN ? "
            "  WHEN bizcircle IN (SELECT bizcircle FROM roughcast_communities "
            "                     WHERE reference_run_id IS NOT NULL "
            "                       AND bizcircle IS NOT NULL) THEN ? "
            "  ELSE ? "
            "END "
            "WHERE is_active = 1",
            (SCORE_SOURCE_SELF, SCORE_SOURCE_NEIGHBOR, SCORE_SOURCE_FALLBACK),
        )
        counts = db.execute(
            "SELECT score_source, COUNT(*) AS n FROM roughcast_listing_current "
            "WHERE is_active = 1 GROUP BY score_source",
        ).fetchall()
        result: dict[str, int] = {src: 0 for src in SCORE_SOURCES}
        for row in counts:
            result[str(row["score_source"])] = int(row["n"])
        return result

    def list_active_listings(self) -> list[sqlite3.Row]:
        """被排序集 P:当前在架清水房。覆盖率分母。"""
        with self.database.connect() as db:
            return db.execute(
                "SELECT listing_id, community_id, community_name, rent_mode, rooms, halls, "
                "       baths, area_sqm, monthly_rent_yuan, fitment_status, bizcircle, "
                "       last_seen_run_id "
                "FROM roughcast_listing_current WHERE is_active = 1 "
                "ORDER BY listing_id"
            ).fetchall()

    def list_pointer_references(self) -> list[sqlite3.Row]:
        """当前参考集 R:只读 `reference_run_id` 指向的那一批,含全部 fitment。

        过滤 P/R/未知由调用方做。禁止 `max(run_id)`。
        """
        with self.database.connect() as db:
            return db.execute(
                "SELECT s.listing_id, s.community_id, s.rent_mode, s.rooms, s.halls, "
                "       s.baths, s.area_sqm, s.monthly_rent_yuan, s.unit_rent, "
                "       s.fitment_status, s.captured_at, s.run_id, c.bizcircle, c.name "
                "FROM roughcast_community_reference_snapshot s "
                "JOIN roughcast_communities c "
                "  ON c.id = s.community_id AND c.reference_run_id = s.run_id "
                "ORDER BY s.community_id, s.listing_id"
            ).fetchall()

    def list_communities_meta(self) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            return db.execute(
                "SELECT id, name, resblock_id, bizcircle, reference_run_id, roughcast_count "
                "FROM roughcast_communities"
            ).fetchall()

    def start_score_run(self, *, model_version: str, delta_version: int,
                        k_scale: float, listing_run_id: int | None) -> int:
        with self.database.connect() as db:
            cursor = db.execute(
                "INSERT INTO roughcast_score_runs "
                "(status, started_at, model_version, delta_version, k_scale, listing_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("RUNNING", self._now(), model_version, delta_version, k_scale, listing_run_id),
            )
            return int(cursor.lastrowid)

    def complete_score_run(
        self, run_id: int, scores: Sequence[object], *,
        delta_value: float, delta_note: str,
        model_version: str, delta_version: int,
    ) -> None:
        """一次事务写入全部分数并收尾。Shadow Run 与将来正式评分共用。"""
        now = self._now()
        rows = [_score_row(run_id, score, model_version, delta_version, delta_value, now)
                for score in scores]
        counts = {
            "scored": sum(1 for score in scores if getattr(score, "quality_status") == "scored"),
            "nearby": sum(1 for score in scores if getattr(score, "quality_status") == "nearby_estimate"),
            "insufficient": sum(1 for score in scores if getattr(score, "quality_status") == "insufficient"),
            "data_error": sum(1 for score in scores if getattr(score, "quality_status") == "data_error"),
            "extreme": sum(1 for score in scores if getattr(score, "extreme_price") == 1),
        }
        with self.database.connect() as db:
            db.executemany(
                "INSERT INTO roughcast_listing_scores ("
                " listing_id, score_run_id, listing_run_id, reference_run_id, "
                " model_version, delta_version, delta_value, unit_rent, "
                " reference_unit_rent, expected_unit_rent, advantage, "
                " quality_score_raw, quality_score, quality_status, quality_tier, "
                " confidence_score, city_rank, peer_scope, comparable_grade, "
                " benchmark_mode, effective_sample_count, reference_age_days, "
                " reference_community_count, reference_spread, extreme_price, "
                " reason, benchmark_pool_json, computed_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            db.execute(
                "UPDATE roughcast_score_runs SET status=?, finished_at=?, delta_value=?, "
                "delta_note=?, scored_count=?, nearby_count=?, insufficient_count=?, "
                "data_error_count=?, extreme_count=? WHERE id=? AND status=?",
                ("COMPLETE", now, delta_value, delta_note, counts["scored"], counts["nearby"],
                 counts["insufficient"], counts["data_error"], counts["extreme"],
                 run_id, "RUNNING"),
            )

    def fail_score_run(self, run_id: int, reason: str) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE roughcast_score_runs SET status=?, finished_at=?, abort_reason=? "
                "WHERE id=? AND status=?",
                ("FAILED", self._now(), reason, run_id, "RUNNING"),
            )

    def latest_score_run(self) -> sqlite3.Row | None:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_score_runs WHERE status='COMPLETE' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def scores_for_run(self, run_id: int) -> list[sqlite3.Row]:
        with self.database.connect() as db:
            return db.execute(
                "SELECT * FROM roughcast_listing_scores WHERE score_run_id=? "
                "ORDER BY city_rank IS NULL, city_rank, listing_id",
                (run_id,),
            ).fetchall()

    def list_score_review_rows(self, run_id: int) -> list[sqlite3.Row]:
        """核分页:分数行 + 当前态封面/户型/租金。

        ranked 页 (Phase 5) 共用本查询,多选的列只读,review 不会受影响。
        多出:`s.quality_tier / quality_status / peer_scope` (供 nearby 渲染),
        `s.quality_score_raw` (排序键,整数化前的真实分),
        `s.confidence_score` (已在 review 用过,这里写明以免读者跳到 SELECT 找),
        `c.orientation / floor_desc / create_time / last_seen_at`
        (ranked 页 `latest` 排序与卡片展示需要)。
        """
        with self.database.connect() as db:
            return db.execute(
                "SELECT s.listing_id, s.city_rank, s.quality_score, s.quality_score_raw, "
                "       s.confidence_score, s.quality_status, s.quality_tier, "
                "       s.benchmark_mode, s.peer_scope, "
                "       s.extreme_price, s.unit_rent, s.reference_unit_rent, "
                "       s.expected_unit_rent, s.advantage, s.reason, "
                "       c.community_name, c.layout, c.rooms, c.halls, c.baths, "
                "       c.area_sqm, c.monthly_rent_yuan, c.title_image_url, "
                "       c.orientation, c.floor_desc, c.create_time, c.last_seen_at, "
                "       c.community_id, c.bizcircle, c.rent_mode, "
                "       comm.latitude AS community_latitude, "
                "       comm.longitude AS community_longitude, "
                "       comm.district AS persisted_district, "
                "       comm.district_status AS district_status, "
                "       comm.districts_json AS districts_json "
                "FROM roughcast_listing_scores s "
                "JOIN roughcast_listing_current c ON c.listing_id = s.listing_id "
                "LEFT JOIN roughcast_communities comm ON comm.id = c.community_id "
                "WHERE s.score_run_id = ?",
                (run_id,),
            ).fetchall()

    def review_view_counts(self) -> dict[str, int]:
        with self.database.connect() as db:
            try:
                rows = db.execute(
                    "SELECT listing_id, view_count FROM roughcast_review_views"
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
        return {str(row["listing_id"]): int(row["view_count"] or 0) for row in rows}

    def increment_review_view(self, listing_id: str) -> int:
        now = self._now()
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO roughcast_review_views "
                "(listing_id, view_count, first_viewed_at, last_viewed_at) "
                "VALUES (?, 1, ?, ?) "
                "ON CONFLICT(listing_id) DO UPDATE SET "
                "view_count = view_count + 1, last_viewed_at = excluded.last_viewed_at",
                (listing_id, now, now),
            )
            count = db.execute(
                "SELECT view_count FROM roughcast_review_views WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
        return int(count["view_count"] if count is not None else 1)

    def reference_snapshot_for(self, community_id: str) -> list[sqlite3.Row]:
        """本小区当前参考集 = 指针指向的那一批,不是 max(run_id)。"""
        with self.database.connect() as db:
            community = db.execute(
                "SELECT reference_run_id FROM roughcast_communities WHERE id = ?",
                (community_id,),
            ).fetchone()
            if community is None or community["reference_run_id"] is None:
                return []
            return db.execute(
                "SELECT * FROM roughcast_community_reference_snapshot "
                "WHERE community_id = ? AND run_id = ? ORDER BY listing_id",
                (community_id, int(community["reference_run_id"])),
            ).fetchall()

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
                "referenced_communities": _scalar(
                    db,
                    "SELECT COUNT(*) FROM roughcast_communities "
                    "WHERE reference_run_id IS NOT NULL",
                ),
                "reference_rows": _scalar(
                    db, "SELECT COUNT(*) FROM roughcast_community_reference_snapshot"
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


def _score_row(run_id: int, score: object, model_version: str, delta_version: int,
               delta_value: float, now: str) -> tuple:
    return (
        getattr(score, "listing_id"), run_id,
        getattr(score, "listing_run_id"), getattr(score, "reference_run_id"),
        model_version, delta_version, delta_value, getattr(score, "unit_rent"),
        getattr(score, "reference_unit_rent"), getattr(score, "expected_unit_rent"),
        getattr(score, "advantage"), getattr(score, "quality_score_raw"),
        getattr(score, "quality_score"), getattr(score, "quality_status"),
        getattr(score, "quality_tier"), getattr(score, "confidence_score"),
        getattr(score, "city_rank"), getattr(score, "peer_scope"),
        getattr(score, "comparable_grade"), getattr(score, "benchmark_mode"),
        getattr(score, "effective_sample_count"), getattr(score, "reference_age_days"),
        getattr(score, "reference_community_count"), getattr(score, "reference_spread"),
        getattr(score, "extreme_price"), getattr(score, "reason"),
        getattr(score, "benchmark_pool_json"), now,
    )


def queue_b_bucket(community_id: str) -> str:
    """stage.bucket 在队列 B 里标记「这批行属于哪个小区」。"""
    return f"resblock={community_id}"


def _queue_b_bucket(community_id: str) -> str:
    return queue_b_bucket(community_id)


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
