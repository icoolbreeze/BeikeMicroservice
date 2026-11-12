"""SQLite store for offline floor-plan label pipeline (stdlib sqlite3 only)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_observation (
    image_url TEXT PRIMARY KEY,
    image_sha256 TEXT,
    image_kind TEXT,
    readability TEXT,
    status TEXT,
    labels_json TEXT,
    derived_json TEXT,
    model_id TEXT,
    prompt_version TEXT,
    labeled_at TEXT,
    error TEXT,
    schema_version TEXT,
    rules_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_observation_sha ON image_observation(image_sha256, model_id, prompt_version);

CREATE TABLE IF NOT EXISTS image_link (
    biz TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    image_url TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (biz, listing_id, image_url)
);
CREATE INDEX IF NOT EXISTS idx_image_link_url ON image_link (image_url);

CREATE TABLE IF NOT EXISTS listing_vision (
    biz TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    floor_plan_status TEXT NOT NULL,
    vision_status TEXT NOT NULL,
    has_balcony INTEGER,
    has_garden INTEGER,
    has_terrace INTEGER,
    has_study INTEGER,
    is_shop INTEGER,
    is_office INTEGER,
    is_residential INTEGER,
    is_parking INTEGER,
    is_ground_shop INTEGER,
    is_photo_not_plan INTEGER,
    rooms_conflict INTEGER,
    use_conflict INTEGER,
    use_kind TEXT,
    balcony_count INTEGER,
    balcony_kinds TEXT,
    garden_kind TEXT,
    garden_position TEXT,
    shop_level TEXT,
    source_url TEXT,
    image_set_sha256 TEXT,
    model_id TEXT,
    prompt_version TEXT,
    labeled_at TEXT,
    schema_version TEXT,
    rules_version TEXT,
    PRIMARY KEY (biz, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_vision_status ON listing_vision (biz, vision_status);

CREATE TABLE IF NOT EXISTS archive_vision_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class ArchiveVisionStore:
    """Store for vision observations, links, and listing rollup results."""

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def db_path(self) -> str:
        return str(self._path)

    @contextmanager
    def _connect(self):
        with self._lock:
            if not self._initialized:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            try:
                if not self._initialized:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                    for table, col in [
                        ("image_observation", "schema_version"),
                        ("image_observation", "rules_version"),
                        ("listing_vision", "schema_version"),
                        ("listing_vision", "rules_version"),
                    ]:
                        cursor = conn.execute(f"PRAGMA table_info({table})")
                        cols = [row["name"] for row in cursor.fetchall()]
                        if col not in cols:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                    self._initialized = True
                yield conn
                conn.commit()
            finally:
                conn.close()

    def get_observation(self, image_url: str) -> dict | None:
        if not self._path.exists():
            return None
        with self._lock:
            try:
                resolved = self._path.resolve().as_posix()
                conn = sqlite3.connect(f"file:{resolved}?mode=ro&query_only=true", uri=True, timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT * FROM image_observation WHERE image_url = ?", (image_url,)
                    ).fetchone()
                    return dict(row) if row else None
                finally:
                    conn.close()
            except Exception:
                return None

    def upsert_observation(self, image_url: str, **kwargs) -> None:
        with self._connect() as conn:
            cols = ["image_url"] + list(kwargs.keys())
            vals = [image_url] + list(kwargs.values())
            placeholders = ", ".join(["?"] * len(vals))
            col_str = ", ".join(cols)
            updates = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            sql = f"INSERT INTO image_observation ({col_str}) VALUES ({placeholders}) ON CONFLICT(image_url) DO UPDATE SET {updates}"
            conn.execute(sql, vals + list(kwargs.values()))

    def update_image_links(
        self, biz: str, listing_id: str, links: list[tuple[str, str]]
    ) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT image_url FROM image_link WHERE biz = ? AND listing_id = ?",
                (biz, listing_id),
            ).fetchall()
            existing_urls = {r["image_url"] for r in existing}
            new_urls = {url for url, _ in links}
            to_remove = existing_urls - new_urls
            if to_remove:
                conn.executemany(
                    "DELETE FROM image_link WHERE biz = ? AND listing_id = ? AND image_url = ?",
                    [(biz, listing_id, url) for url in to_remove],
                )
            for url, source in links:
                conn.execute(
                    "INSERT INTO image_link (biz, listing_id, image_url, source) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(biz, listing_id, image_url) DO UPDATE SET source = ?",
                    (biz, listing_id, url, source, source),
                )
            if existing_urls != new_urls:
                # Atomically invalidate listing_vision if ready
                conn.execute(
                    "UPDATE listing_vision SET vision_status = 'stale' WHERE biz = ? AND listing_id = ? AND vision_status = 'ready'",
                    (biz, listing_id),
                )

    def upsert_listing_vision(self, biz: str, listing_id: str, data: dict) -> None:
        with self._connect() as conn:
            keys = ["biz", "listing_id"] + list(data.keys())
            vals = [biz, listing_id] + list(data.values())
            placeholders = ", ".join(["?"] * len(vals))
            col_str = ", ".join(keys)
            updates = ", ".join([f"{k} = ?" for k in data.keys()])
            sql = f"INSERT INTO listing_vision ({col_str}) VALUES ({placeholders}) ON CONFLICT(biz, listing_id) DO UPDATE SET {updates}"
            conn.execute(sql, vals + list(data.values()))

    def get_listing_vision(self, biz: str, listing_id: str) -> dict | None:
        if not self._path.exists():
            return None
        with self._lock:
            try:
                resolved = self._path.resolve().as_posix()
                conn = sqlite3.connect(f"file:{resolved}?mode=ro&query_only=true", uri=True, timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute(
                        "SELECT * FROM listing_vision WHERE biz = ? AND listing_id = ?",
                        (biz, listing_id),
                    ).fetchone()
                    return dict(row) if row else None
                finally:
                    conn.close()
            except Exception:
                return None

    def filter_listing_ids(
        self,
        biz: str,
        *,
        has_balcony: int | None = None,
        is_ground_shop: int | None = None,
        use_kind: str | None = None,
    ) -> list[str]:
        if not self._path.exists():
            return []
        with self._lock:
            try:
                resolved = self._path.resolve().as_posix()
                conn = sqlite3.connect(f"file:{resolved}?mode=ro&query_only=true", uri=True, timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    clauses = ["biz = ?", "vision_status = 'ready'"]
                    params: list[object] = [biz]
                    if has_balcony is not None:
                        clauses.append("has_balcony = ?")
                        params.append(has_balcony)
                    if is_ground_shop is not None:
                        clauses.append("is_ground_shop = ?")
                        params.append(is_ground_shop)
                    if use_kind is not None:
                        clauses.append("use_kind = ?")
                        params.append(use_kind)
                    sql = f"SELECT listing_id FROM listing_vision WHERE {' AND '.join(clauses)}"
                    rows = conn.execute(sql, params).fetchall()
                    return [r["listing_id"] for r in rows]
                finally:
                    conn.close()
            except Exception:
                return []

    def get_meta(self, key: str) -> str | None:
        if not self._path.exists():
            return None
        with self._lock:
            try:
                resolved = self._path.resolve().as_posix()
                conn = sqlite3.connect(f"file:{resolved}?mode=ro&query_only=true", uri=True, timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute("SELECT value FROM archive_vision_meta WHERE key = ?", (key,)).fetchone()
                    return row["value"] if row else None
                finally:
                    conn.close()
            except Exception:
                return None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO archive_vision_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
            )
