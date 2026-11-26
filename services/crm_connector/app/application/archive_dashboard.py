"""Read-only archive dashboard queries. No upstream calls and no payload scans."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
STATUSES = ("none", "complete", "failed", "skipped_unsupported")


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def age_seconds(value: str | None) -> float | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
        if moment is None or moment.tzinfo is None:
            return None
        return (datetime.now(UTC) - moment).total_seconds()
    except (ValueError, TypeError, AttributeError):
        return None


@contextmanager
def readonly(path: str):
    target = Path(path)
    if not target.is_file():
        yield None
        return
    # URI read-only mode never creates a missing database or applies migrations.
    conn = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def archive_snapshot(path: str, ttl_days: int) -> dict[str, Any]:
    key = (str(Path(path).resolve()), ttl_days)
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and time.monotonic() - entry[0] < 5:
            return entry[1]
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=ttl_days)).isoformat()
    fields = {
        "total": "COUNT(*)",
        "active": "SUM(is_active = 1)",
        "complete": "SUM(detail_status = 'complete')",
        "pending": "SUM(detail_status = 'none')",
        "failed": "SUM(detail_status = 'failed')",
        "skipped": "SUM(detail_status = 'skipped_unsupported')",
        "active_pending": "SUM(is_active = 1 AND detail_status = 'none')",
        "active_failed": "SUM(is_active = 1 AND detail_status = 'failed')",
        "active_skipped": "SUM(is_active = 1 AND detail_status = 'skipped_unsupported')",
        "active_unknown": "SUM(is_active = 1 AND detail_status NOT IN ('none','complete','failed','skipped_unsupported'))",
    }
    fresh = "(julianday(detail_captured_at) > julianday(:cutoff) AND julianday(detail_captured_at) <= julianday(:now))"
    fields["active_fresh"] = "SUM(is_active = 1 AND detail_status = 'complete' AND " + fresh + ")"
    fields["active_stale"] = (
        "SUM(is_active = 1 AND detail_status = 'complete' AND NOT COALESCE(" + fresh + ", 0))"
    )
    select = ", ".join(f"COALESCE({sql}, 0) AS {name}" for name, sql in fields.items())
    result: dict[str, Any] = {
        "available": False,
        "reason": "source_db_missing",
        "computed_at": now.isoformat(),
        "ttl_days": ttl_days,
        "totals": None,
        "districts": [],
        "history": [],
        "last_progress_at": None,
        "last_success_at": None,
        "coverage_note": "旧归档未记录完整行政区轮次；不依据入库数量或价格分片推断扫描完成。",
    }
    with readonly(path) as conn:
        if conn is None:
            return result
        # All figures in this response use one SQLite read transaction.
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT district_id, "
            + select
            + ", MAX(CASE WHEN detail_status = 'complete' THEN detail_captured_at END) AS last_success_at FROM listing_current GROUP BY district_id",
            {"cutoff": cutoff, "now": now.isoformat()},
        ).fetchall()
        districts = [{**dict(row), "inactive": row["total"] - row["active"]} for row in rows]
        totals = {name: sum(row[name] for row in districts) for name in fields}
        totals["inactive"] = totals["total"] - totals["active"]
        cursor = conn.execute("SELECT updated_at FROM district_cursor WHERE id = 1").fetchone()
        success = max(
            (row["last_success_at"] for row in districts if row["last_success_at"]), default=None
        )
        history = [
            dict(row)
            for row in conn.execute(
                "SELECT local_date, request_count, detail_count FROM daily_counters ORDER BY local_date DESC LIMIT 7"
            ).fetchall()
        ][::-1]
        result.update(
            available=True,
            reason=None,
            totals=totals,
            districts=districts,
            last_progress_at=cursor[0] if cursor else None,
            last_success_at=success,
            history=history,
        )
    with _CACHE_LOCK:
        # Configured paths are bounded in normal use; cap cache for test/app factories.
        if len(_CACHE) >= 32:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), result)
    return result


def activity(
    status: dict[str, Any], snapshot: dict[str, Any], max_pause: float = 360
) -> dict[str, Any]:
    counts = status.get("counters") or {}
    reason = None
    if not status.get("enabled"):
        reason = "disabled"
    elif (
        status.get("daily_request_cap", 0) > 0
        and counts.get("request_count", 0) >= status["daily_request_cap"]
    ):
        reason = "request_cap"
    elif not status.get("within_window"):
        reason = "outside_window"
    elif status.get("connection_state") not in ("ready", "expiring"):
        reason = "auth_required"
    elif not status.get("running"):
        reason = "thread_stopped"
    cursor = status.get("cursor") or {}
    progress_age = age_seconds(snapshot.get("last_progress_at"))
    threshold = max(900, max_pause + 180)
    if reason:
        state = (
            "inactive"
            if reason == "disabled"
            else "waiting"
            if reason in ("outside_window", "request_cap")
            else "attention"
        )
    elif progress_age is None or progress_age < -30:
        state = "unknown"
    elif progress_age > threshold:
        state, reason = "attention", "no_recent_progress"
    else:
        state = "alive_with_recent_progress"
    detail_cap = status.get("daily_detail_cap", 0)
    detail_capped = detail_cap > 0 and counts.get("detail_count", 0) >= detail_cap
    return {
        "state": state,
        "reason": reason,
        "thread_alive": bool(status.get("running")),
        "last_progress_at": snapshot.get("last_progress_at"),
        "last_success_at": snapshot.get("last_success_at"),
        "progress_age_seconds": progress_age,
        "stale_after_seconds": threshold,
        "current_action": "list_scan"
        if detail_capped or not cursor.get("pending_detail_count")
        else "detail_backfill",
        "detail_cap_reached": detail_capped,
        "note": "线程存活与业务进展分别判断；限速及短暂休息不会立即判为停滞。",
    }


def issues(
    path: str, *, status: str, district_id: str | None, page: int, page_size: int
) -> dict[str, Any]:
    with readonly(path) as conn:
        if conn is None:
            return {
                "available": False,
                "items": [],
                "total": None,
                "page": page,
                "page_size": page_size,
            }
        where = "detail_status = ?"
        params: list[Any] = [status]
        if district_id:
            where += " AND district_id = ?"
            params.append(district_id)
        conn.execute("BEGIN")
        total = conn.execute(
            "SELECT COUNT(*) FROM listing_current WHERE " + where, params
        ).fetchone()[0]
        # Source detail status and its capture time are authoritative. Never return
        # raw upstream errors/stack traces. crawl_log IDs can be correlated privately.
        rows = conn.execute(
            "SELECT listing_id, district_id, community, detail_status, detail_captured_at, is_active "
            "FROM listing_current WHERE "
            + where
            + " ORDER BY detail_captured_at DESC, listing_id LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        items = []
        has_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_crawl_log_listing_id'"
        ).fetchone()
        for row in rows:
            item = dict(row)
            log = (
                conn.execute(
                    "SELECT id, note, requested_at FROM crawl_log WHERE listing_id = ? "
                    "AND status IN ('error','skipped') ORDER BY id DESC LIMIT 1",
                    (row["listing_id"],),
                ).fetchone()
                if has_index
                else None
            )
            code = log["note"] if log else None
            # Uppercase fixed codes only; messages, URLs and tokens are not exposed.
            code = (
                code
                if isinstance(code, str)
                and re.fullmatch(r"(?:CRM_|UPSTREAM_)[A-Z0-9_]{1,64}", code)
                else None
            )
            item.update(
                error_code=code
                or ("UNSUPPORTED_TYPE" if status == "skipped_unsupported" else "DETAIL_FAILED"),
                summary="类型暂不支持"
                if status == "skipped_unsupported"
                else "详情处理失败；可按关联日志编号排查",
                log_id=log["id"] if log else None,
                attempted_at=log["requested_at"] if log else row["detail_captured_at"],
            )
            items.append(item)
        return {
            "available": True,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def vision_status(vision_db: str, source_paths: dict[str, str]) -> dict[str, Any]:
    from app.application.archive_vision import (
        MODEL_ID,
        PROMPT_VERSION,
        RULES_VERSION,
        SCHEMA_VERSION,
    )

    response: dict[str, Any] = {
        "computed_at": timestamp(),
        "available": False,
        "worker_state": "unknown",
        "heartbeat": None,
        "metrics": None,
        "businesses": {},
        "total_listings_with_plan": None,
        "pending_count": None,
        "cache_hit_rate": None,
        "coverage_reason": "全库有图房源数量尚未完成核对，待识别数量和完成率暂不可确认。",
        "validity_note": "以下为存储结果快照；版本匹配不代表已重新核对所有源图片变更。",
    }
    # A tiny sidecar carries liveness before the worker is allowed to create its DB.
    try:
        sidecar = Path(vision_db + ".status.json")
        if sidecar.is_file() and sidecar.stat().st_size <= 16384:
            heartbeat = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(heartbeat, dict):
                response["heartbeat"] = heartbeat
                age = age_seconds(heartbeat.get("updated_at"))
                state = heartbeat.get("state")
                response["worker_state"] = (
                    state
                    if state in ("stopped", "error")
                    else "unknown"
                    if age is None or age < -30
                    else "stale"
                    if age > 90
                    else state
                    if state in ("running", "idle", "paused_storage")
                    else "unknown"
                )
    except (OSError, ValueError):
        pass
    with readonly(vision_db) as conn:
        if conn is None:
            response["reason"] = "vision_db_missing"
            return response
        response["available"] = True
        for key, target in (("dashboard:heartbeat", "heartbeat"), ("dashboard:batch", "metrics")):
            row = conn.execute(
                "SELECT value FROM archive_vision_meta WHERE key = ?", (key,)
            ).fetchone()
            if row:
                try:
                    parsed = json.loads(row[0])
                    response[target] = parsed if isinstance(parsed, dict) else None
                except (TypeError, ValueError):
                    pass
        heartbeat = response["heartbeat"] or {}
        age = age_seconds(heartbeat.get("updated_at"))
        state = heartbeat.get("state")
        terminal = state in ("stopped", "error")
        response["worker_state"] = (
            state
            if terminal
            else "unknown"
            if age is None or age < -30
            else "stale"
            if age > 90
            else state
            if state in ("running", "idle", "paused_storage")
            else "unknown"
        )
        response["heartbeat_age_seconds"] = age
        for biz, path in source_paths.items():
            storage_biz = "rent" if biz == "rental" else biz
            active = None
            known_active = None
            with readonly(path) as source:
                if source is not None:
                    active = source.execute(
                        "SELECT COUNT(*) FROM listing_current WHERE is_active = 1"
                    ).fetchone()[0]
            # Source membership is checked by an indexed join; no payload parsing.
            if Path(path).is_file():
                conn.execute(
                    "ATTACH DATABASE ? AS source_db", (Path(path).resolve().as_uri() + "?mode=ro",)
                )
                try:
                    rows = conn.execute(
                        "SELECT v.vision_status, COUNT(*) AS n FROM listing_vision v "
                        "JOIN source_db.listing_current s ON s.listing_id = v.listing_id "
                        "WHERE v.biz = ? AND s.is_active = 1 GROUP BY v.vision_status",
                        (storage_biz,),
                    ).fetchall()
                    counts = {row["vision_status"]: row["n"] for row in rows}
                    known_active = sum(counts.values())
                    version_count = conn.execute(
                        "SELECT COUNT(*) FROM listing_vision v JOIN source_db.listing_current s "
                        "ON s.listing_id = v.listing_id WHERE v.biz = ? AND s.is_active = 1 "
                        "AND v.model_id=? AND v.prompt_version=? AND v.schema_version=? AND v.rules_version=?",
                        (storage_biz, MODEL_ID, PROMPT_VERSION, SCHEMA_VERSION, RULES_VERSION),
                    ).fetchone()[0]
                finally:
                    conn.execute("DETACH DATABASE source_db")
            else:
                counts, version_count = {}, None
            image_counts = {
                row["status"] or "unknown": row["n"]
                for row in conn.execute(
                    "SELECT o.status, COUNT(DISTINCT o.image_url) AS n FROM image_observation o "
                    "JOIN image_link l ON l.image_url = o.image_url WHERE l.biz = ? GROUP BY o.status",
                    (storage_biz,),
                ).fetchall()
            }
            source_kinds = {
                row["source"]: row["n"]
                for row in conn.execute(
                    "SELECT source, COUNT(*) AS n FROM image_link WHERE biz=? GROUP BY source",
                    (storage_biz,),
                ).fetchall()
            }
            response["businesses"][biz] = {
                "source_active": active,
                "stored_listing_results": known_active,
                "source_without_summary": max(0, active - known_active)
                if active is not None and known_active is not None
                else None,
                "stored_status_counts": counts,
                "current_version_results": version_count,
                "linked_image_status_counts": image_counts,
                "image_link_sources": source_kinds,
                "pending_count": None,
                "effective_valid_count": None,
            }
        response["image_observation_counts"] = {
            row["status"] or "unknown": row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM image_observation GROUP BY status"
            ).fetchall()
        }
    return response
