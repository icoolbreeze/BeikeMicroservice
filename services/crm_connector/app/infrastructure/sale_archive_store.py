"""SQLite archive for localized 买卖 listings (stdlib ``sqlite3`` only).

A separate database file from both the credential store and the rental
archive: it holds business data only, never authentication material. Every
payload written here has already passed
``sanitize_sale_archive_payload``. The ``price_band`` column stores the full
unit signature (price, optionally area and room), not the price token alone,
so a finished slice can only deactivate listings from that same slice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.sale_archive_plan import (
    ArchiveCursor,
    ArchiveUnit,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listing_current (
    listing_id TEXT PRIMARY KEY,
    district_id TEXT,
    price_band TEXT,
    del_type INTEGER,
    community TEXT,
    payload_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    detail_status TEXT NOT NULL DEFAULT 'none',
    detail_captured_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_seen_run_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_listing_current_district
    ON listing_current (district_id, is_active);
CREATE INDEX IF NOT EXISTS idx_listing_current_detail_status
    ON listing_current (detail_status);
CREATE INDEX IF NOT EXISTS idx_listing_current_district_detail
    ON listing_current (district_id, detail_status, is_active);

CREATE TABLE IF NOT EXISTS listing_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_run_id INTEGER,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_snapshot_listing
    ON listing_snapshot (listing_id, captured_at);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    district_id TEXT,
    price_band TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pages_done INTEGER NOT NULL DEFAULT 0,
    items_seen INTEGER NOT NULL DEFAULT 0,
    request_count INTEGER NOT NULL DEFAULT 0,
    detail_count INTEGER NOT NULL DEFAULT 0,
    abort_reason TEXT
);

CREATE TABLE IF NOT EXISTS crawl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    requested_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    listing_id TEXT,
    status TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS district_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    district_index INTEGER NOT NULL,
    price_band TEXT,
    extra_filters TEXT NOT NULL DEFAULT '[]',
    pending_units TEXT NOT NULL DEFAULT '[]',
    page INTEGER NOT NULL,
    run_id INTEGER,
    seen_ids TEXT NOT NULL DEFAULT '[]',
    pending_detail_ids TEXT NOT NULL DEFAULT '[]',
    detail_run_id INTEGER,
    unit_total INTEGER,
    unit_resumed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_counters (
    local_date TEXT PRIMARY KEY,
    request_count INTEGER NOT NULL DEFAULT 0,
    detail_count INTEGER NOT NULL DEFAULT 0
);
"""

DETAIL_STATUS_NONE = "none"
DETAIL_STATUS_COMPLETE = "complete"
DETAIL_STATUS_SKIPPED = "skipped_unsupported"
DETAIL_STATUS_FAILED = "failed"

LOCALIZED_DETAIL_STATUSES = frozenset({DETAIL_STATUS_COMPLETE, DETAIL_STATUS_SKIPPED})


@dataclass(frozen=True)
class ArchiveListingRow:
    """One list-page row ready to persist (payload already sanitized)."""

    listing_id: str
    district_id: str
    price_band: str | None
    del_type: int | None
    community: str | None
    payload: dict[str, object]
    content_hash: str


@dataclass(frozen=True)
class UpsertOutcome:
    """What the archive knew about a row *before* this list page touched it."""

    content_changed: bool
    detail_status: str
    detail_captured_at: datetime | None


@dataclass(frozen=True)
class StoredListing:
    listing_id: str
    district_id: str | None
    price_band: str | None
    del_type: int | None
    community: str | None
    payload: dict[str, object]
    content_hash: str
    detail_status: str
    detail_captured_at: datetime | None
    first_seen_at: str
    last_seen_at: str
    last_seen_run_id: int | None
    is_active: bool


@dataclass(frozen=True)
class ArchiveRun:
    id: int
    status: str
    district_id: str | None
    price_band: str | None
    started_at: str
    finished_at: str | None
    pages_done: int
    items_seen: int
    request_count: int
    detail_count: int
    abort_reason: str | None


@dataclass(frozen=True)
class DailyCounters:
    local_date: str
    request_count: int
    detail_count: int


@dataclass(frozen=True)
class DistrictStat:
    """Per-district archive counts for the dashboard."""

    district_id: str
    total: int
    active: int
    complete: int
    pending: int
    skipped: int
    failed: int


class SaleArchiveStore:
    """Serialized SQLite access; safe for the daemon thread plus HTTP threads.

    The schema is created on first use rather than at construction so wiring
    the runner into ``create_app`` never touches the filesystem while the
    archive is disabled.
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def db_path(self) -> str:
        return str(self._path)

    def _has_db(self) -> bool:
        """Whether anything has been written yet.

        Read paths consult this so ``GET /archive/sale/status`` on a
        disabled connector reports empty state instead of creating a
        database file as a side effect of being asked.
        """
        return self._initialized or self._path.exists()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if not self._initialized:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self._path), timeout=10.0)
            connection.row_factory = sqlite3.Row
            try:
                if not self._initialized:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(_SCHEMA)
                    self._initialized = True
                yield connection
                connection.commit()
            finally:
                connection.close()

    # -- listings -----------------------------------------------------------

    def upsert_list_row(
        self, row: ArchiveListingRow, *, run_id: int | None, now: datetime
    ) -> UpsertOutcome:
        """Persist a list-page row; always cheap, always allowed.

        Returns the pre-existing detail state so the caller can decide
        whether a detail call is still needed. A detail payload already
        merged into ``payload_json`` survives this write.
        """
        stamp = _iso(now)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json, content_hash, detail_status, detail_captured_at"
                " FROM listing_current WHERE listing_id = ?",
                (row.listing_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO listing_current ("
                    " listing_id, district_id, price_band, del_type, community,"
                    " payload_json, content_hash, detail_status, detail_captured_at,"
                    " first_seen_at, last_seen_at, last_seen_run_id, is_active"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 1)",
                    (
                        row.listing_id, row.district_id, row.price_band, row.del_type,
                        row.community, _dumps(row.payload), row.content_hash,
                        DETAIL_STATUS_NONE, stamp, stamp, run_id,
                    ),
                )
                _insert_snapshot(connection, row, run_id, stamp, row.payload)
                return UpsertOutcome(
                    content_changed=True,
                    detail_status=DETAIL_STATUS_NONE,
                    detail_captured_at=None,
                )
            merged = _loads(existing["payload_json"])
            merged.update(row.payload)
            changed = existing["content_hash"] != row.content_hash
            connection.execute(
                "UPDATE listing_current SET district_id = ?, price_band = ?,"
                " del_type = ?, community = ?, payload_json = ?, content_hash = ?,"
                " last_seen_at = ?, last_seen_run_id = ?, is_active = 1"
                " WHERE listing_id = ?",
                (
                    row.district_id, row.price_band, row.del_type, row.community,
                    _dumps(merged), row.content_hash, stamp, run_id, row.listing_id,
                ),
            )
            if changed:
                _insert_snapshot(connection, row, run_id, stamp, merged)
            return UpsertOutcome(
                content_changed=changed,
                detail_status=str(existing["detail_status"] or DETAIL_STATUS_NONE),
                detail_captured_at=_parse_iso(existing["detail_captured_at"]),
            )

    def mark_detail(
        self,
        listing_id: str,
        *,
        status: str,
        detail: dict[str, object] | None,
        captured_at: datetime,
    ) -> None:
        """Merge a sanitized detail payload and record the localization stamp."""
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM listing_current WHERE listing_id = ?",
                (listing_id,),
            ).fetchone()
            if existing is None:
                return
            payload = _loads(existing["payload_json"])
            if detail is not None:
                payload["detail"] = detail
            connection.execute(
                "UPDATE listing_current SET payload_json = ?, detail_status = ?,"
                " detail_captured_at = ? WHERE listing_id = ?",
                (_dumps(payload), status, _iso(captured_at), listing_id),
            )

    def get_listing(self, listing_id: str) -> StoredListing | None:
        if not self._has_db():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM listing_current WHERE listing_id = ?", (listing_id,)
            ).fetchone()
        return _stored_listing(row) if row is not None else None

    def list_listings(
        self,
        *,
        district_id: str | None = None,
        active: bool | None = True,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[tuple[StoredListing, ...], int]:
        if not self._has_db():
            return (), 0
        clauses: list[str] = []
        params: list[object] = []
        if district_id:
            clauses.append("district_id = ?")
            params.append(district_id)
        if active is not None:
            clauses.append("is_active = ?")
            params.append(1 if active else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = max(page - 1, 0) * max(page_size, 1)
        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS total FROM listing_current{where}", params
                ).fetchone()["total"]
            )
            rows = connection.execute(
                f"SELECT * FROM listing_current{where}"
                " ORDER BY last_seen_at DESC, listing_id ASC LIMIT ? OFFSET ?",
                (*params, max(page_size, 1), offset),
            ).fetchall()
        return tuple(_stored_listing(row) for row in rows), total

    def counts(self) -> tuple[int, int]:
        """Return ``(total, active)`` listing counts."""
        if not self._has_db():
            return 0, 0
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active"
                " FROM listing_current"
            ).fetchone()
        return int(row["total"]), int(row["active"])

    def detail_counts(self) -> dict[str, int]:
        """Counts by ``detail_status`` (empty file → zeros)."""
        empty = {
            DETAIL_STATUS_NONE: 0,
            DETAIL_STATUS_COMPLETE: 0,
            DETAIL_STATUS_SKIPPED: 0,
            DETAIL_STATUS_FAILED: 0,
        }
        if not self._has_db():
            return empty
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT detail_status, COUNT(*) AS n FROM listing_current"
                " GROUP BY detail_status"
            ).fetchall()
        out = dict(empty)
        for row in rows:
            out[str(row["detail_status"])] = int(row["n"])
        return out

    def district_stats(self) -> tuple[DistrictStat, ...]:
        if not self._has_db():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT district_id,"
                " COUNT(*) AS total,"
                " COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active,"
                " COALESCE(SUM(CASE WHEN detail_status = ? THEN 1 ELSE 0 END), 0) AS complete,"
                " COALESCE(SUM(CASE WHEN detail_status = ? THEN 1 ELSE 0 END), 0) AS pending,"
                " COALESCE(SUM(CASE WHEN detail_status = ? THEN 1 ELSE 0 END), 0) AS skipped,"
                " COALESCE(SUM(CASE WHEN detail_status = ? THEN 1 ELSE 0 END), 0) AS failed"
                " FROM listing_current GROUP BY district_id"
                " ORDER BY district_id",
                (
                    DETAIL_STATUS_COMPLETE,
                    DETAIL_STATUS_NONE,
                    DETAIL_STATUS_SKIPPED,
                    DETAIL_STATUS_FAILED,
                ),
            ).fetchall()
        return tuple(
            DistrictStat(
                district_id=str(row["district_id"] or ""),
                total=int(row["total"]),
                active=int(row["active"]),
                complete=int(row["complete"]),
                pending=int(row["pending"]),
                skipped=int(row["skipped"]),
                failed=int(row["failed"]),
            )
            for row in rows
        )

    def deactivate_unseen(
        self, *, district_id: str, price_band: str | None, run_id: int
    ) -> int:
        """Flip listings the completed unit did not see to ``is_active=0``.

        Scoped to the unit signature that actually completed: an unsliced
        district run (signature NULL) covers the whole district, while a
        price/area/room slice can only speak for rows last seen under that
        same signature. Deactivating a whole district off one slice would
        wrongly retire listings outside it.
        """
        clauses = ["district_id = ?", "is_active = 1", "last_seen_run_id IS NOT ?"]
        params: list[object] = [district_id, run_id]
        if price_band is not None:
            clauses.insert(1, "price_band = ?")
            params.insert(1, price_band)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE listing_current SET is_active = 0 WHERE {' AND '.join(clauses)}",
                params,
            )
        return int(cursor.rowcount or 0)

    # -- runs and logs ------------------------------------------------------

    def start_run(
        self, *, district_id: str, price_band: str | None, started_at: datetime
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO crawl_runs (status, district_id, price_band, started_at)"
                " VALUES ('RUNNING', ?, ?, ?)",
                (district_id, price_band, _iso(started_at)),
            )
        return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        finished_at: datetime,
        abort_reason: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE crawl_runs SET status = ?, finished_at = ?, abort_reason = ?"
                " WHERE id = ?",
                (status, _iso(finished_at), abort_reason, run_id),
            )

    def bump_run(
        self,
        run_id: int | None,
        *,
        pages: int = 0,
        items: int = 0,
        requests: int = 0,
        details: int = 0,
    ) -> None:
        if run_id is None:
            return
        with self._connect() as connection:
            connection.execute(
                "UPDATE crawl_runs SET pages_done = pages_done + ?,"
                " items_seen = items_seen + ?, request_count = request_count + ?,"
                " detail_count = detail_count + ? WHERE id = ?",
                (pages, items, requests, details, run_id),
            )

    def latest_run(self) -> ArchiveRun | None:
        if not self._has_db():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return _archive_run(row) if row is not None else None

    def get_run(self, run_id: int) -> ArchiveRun | None:
        if not self._has_db():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return _archive_run(row) if row is not None else None

    def log_call(
        self,
        *,
        run_id: int | None,
        kind: str,
        listing_id: str | None,
        status: str,
        note: str | None,
        requested_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO crawl_log (run_id, requested_at, kind, listing_id,"
                " status, note) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, _iso(requested_at), kind, listing_id, status, note),
            )

    def log_entries(self, limit: int = 20) -> tuple[dict[str, object], ...]:
        if not self._has_db():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM crawl_log ORDER BY id DESC LIMIT ?", (max(limit, 1),)
            ).fetchall()
        return tuple(dict(row) for row in rows)

    # -- cursor -------------------------------------------------------------

    def load_cursor(self) -> ArchiveCursor | None:
        if not self._has_db():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM district_cursor WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        columns = row.keys()
        price_band = row["price_band"] if "price_band" in columns else None
        pending_units = (
            _loads_units(row["pending_units"]) if "pending_units" in columns else ()
        )
        extra_filters = (
            _loads_pairs(row["extra_filters"]) if "extra_filters" in columns else ()
        )
        return ArchiveCursor(
            district_index=int(row["district_index"]),
            price_band=None if price_band is None else str(price_band),
            extra_filters=extra_filters,
            pending_units=pending_units,
            page=int(row["page"]),
            run_id=None if row["run_id"] is None else int(row["run_id"]),
            seen_ids=tuple(_loads_list(row["seen_ids"])),
            pending_detail_ids=tuple(_loads_list(row["pending_detail_ids"])),
            detail_run_id=(
                None if row["detail_run_id"] is None else int(row["detail_run_id"])
            ),
            unit_total=None if row["unit_total"] is None else int(row["unit_total"]),
            unit_resumed=bool(row["unit_resumed"]),
        )

    def save_cursor(self, cursor: ArchiveCursor, updated_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO district_cursor (id, district_index,"
                " price_band, extra_filters, pending_units, page,"
                " run_id, seen_ids, pending_detail_ids, detail_run_id, unit_total,"
                " unit_resumed, updated_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET district_index = excluded.district_index,"
                " price_band = excluded.price_band,"
                " extra_filters = excluded.extra_filters,"
                " pending_units = excluded.pending_units,"
                " page = excluded.page,"
                " run_id = excluded.run_id, seen_ids = excluded.seen_ids,"
                " pending_detail_ids = excluded.pending_detail_ids,"
                " detail_run_id = excluded.detail_run_id,"
                " unit_total = excluded.unit_total,"
                " unit_resumed = excluded.unit_resumed,"
                " updated_at = excluded.updated_at",
                (
                    cursor.district_index, cursor.price_band,
                    _dumps_pairs(cursor.extra_filters),
                    _dumps_units(cursor.pending_units), cursor.page,
                    cursor.run_id, _dumps(list(cursor.seen_ids)),
                    _dumps(list(cursor.pending_detail_ids)), cursor.detail_run_id,
                    cursor.unit_total, 1 if cursor.unit_resumed else 0,
                    _iso(updated_at),
                ),
            )

    # -- daily counters -----------------------------------------------------

    def daily_counters(self, local_date: str) -> DailyCounters:
        if not self._has_db():
            return DailyCounters(local_date=local_date, request_count=0, detail_count=0)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_counters WHERE local_date = ?", (local_date,)
            ).fetchone()
        if row is None:
            return DailyCounters(local_date=local_date, request_count=0, detail_count=0)
        return DailyCounters(
            local_date=local_date,
            request_count=int(row["request_count"]),
            detail_count=int(row["detail_count"]),
        )

    def bump_daily(self, local_date: str, *, requests: int = 0, details: int = 0) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO daily_counters (local_date, request_count, detail_count)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(local_date) DO UPDATE SET"
                " request_count = request_count + excluded.request_count,"
                " detail_count = detail_count + excluded.detail_count",
                (local_date, requests, details),
            )


def _insert_snapshot(
    connection: sqlite3.Connection,
    row: ArchiveListingRow,
    run_id: int | None,
    stamp: str,
    payload: dict[str, object],
) -> None:
    connection.execute(
        "INSERT INTO listing_snapshot (listing_id, captured_at, captured_run_id,"
        " content_hash, payload_json) VALUES (?, ?, ?, ?, ?)",
        (row.listing_id, stamp, run_id, row.content_hash, _dumps(payload)),
    )


def _stored_listing(row: sqlite3.Row) -> StoredListing:
    return StoredListing(
        listing_id=str(row["listing_id"]),
        district_id=row["district_id"],
        price_band=row["price_band"],
        del_type=None if row["del_type"] is None else int(row["del_type"]),
        community=row["community"],
        payload=_loads(row["payload_json"]),
        content_hash=str(row["content_hash"]),
        detail_status=str(row["detail_status"]),
        detail_captured_at=_parse_iso(row["detail_captured_at"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        last_seen_run_id=(
            None if row["last_seen_run_id"] is None else int(row["last_seen_run_id"])
        ),
        is_active=bool(row["is_active"]),
    )


def _archive_run(row: sqlite3.Row) -> ArchiveRun:
    return ArchiveRun(
        id=int(row["id"]),
        status=str(row["status"]),
        district_id=row["district_id"],
        price_band=row["price_band"],
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        pages_done=int(row["pages_done"]),
        items_seen=int(row["items_seen"]),
        request_count=int(row["request_count"]),
        detail_count=int(row["detail_count"]),
        abort_reason=row["abort_reason"],
    )


def _dumps_units(units: tuple[ArchiveUnit, ...]) -> str:
    return _dumps(
        [
            {
                "price_band": unit.price_band,
                "extra_filters": [list(pair) for pair in unit.extra_filters],
            }
            for unit in units
        ]
    )


def _loads_units(raw: object) -> tuple[ArchiveUnit, ...]:
    if not raw:
        return ()
    value = json.loads(str(raw))
    if not isinstance(value, list):
        return ()
    units: list[ArchiveUnit] = []
    for item in value:
        if not isinstance(item, dict) or "price_band" not in item:
            continue
        band = item["price_band"]
        units.append(
            ArchiveUnit(
                None if band is None else str(band),
                _pairs_from_json(item.get("extra_filters")),
            )
        )
    return tuple(units)


def _dumps_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    return _dumps([[key, val] for key, val in pairs])


def _loads_pairs(raw: object) -> tuple[tuple[str, str], ...]:
    return _pairs_from_json(raw)


def _pairs_from_json(raw: object) -> tuple[tuple[str, str], ...]:
    if not raw:
        return ()
    value = json.loads(str(raw)) if isinstance(raw, str) else raw
    if not isinstance(value, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
    return tuple(pairs)


def _dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(raw: object) -> dict[str, object]:
    if not raw:
        return {}
    value = json.loads(str(raw))
    return value if isinstance(value, dict) else {}


def _loads_list(raw: object) -> list[str]:
    if not raw:
        return []
    value = json.loads(str(raw))
    return [str(item) for item in value] if isinstance(value, list) else []


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_iso(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
