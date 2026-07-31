from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from app.domain.models import MediaItem, MediaType, Role, Store, User


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS stores (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    region_id TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    region_id TEXT,
                    store_id TEXT REFERENCES stores(id),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    created_by TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media_items (
                    id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL REFERENCES stores(id),
                    title TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    image_duration_seconds REAL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    is_published INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_users_scope ON users(region_id, store_id);
                CREATE INDEX IF NOT EXISTS idx_media_playlist
                    ON media_items(store_id, is_published, sort_order, created_at);
                """
            )

    def user_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, *, user_id: str, username: str, display_name: str,
                    password_hash: str, role: Role, region_id: str | None,
                    store_id: str | None, created_by: str | None = None) -> User:
        created_at = _now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (user_id, username.lower(), display_name, password_hash, role.value,
                 region_id, store_id, created_at, created_by),
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> User | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_credentials(self, username: str) -> tuple[User, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        return (_user(row), row["password_hash"]) if row else None

    def list_users(self) -> list[User]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [_user(row) for row in rows]

    def update_user(self, user_id: str, *, display_name: str, role: Role,
                    region_id: str | None, store_id: str | None, is_active: bool,
                    password_hash: str | None = None) -> User:
        fields = "display_name = ?, role = ?, region_id = ?, store_id = ?, is_active = ?"
        values: list[object] = [display_name, role.value, region_id, store_id, int(is_active)]
        if password_hash:
            fields += ", password_hash = ?"
            values.append(password_hash)
        values.append(user_id)
        with self.connect() as db:
            db.execute(f"UPDATE users SET {fields} WHERE id = ?", values)
            if not is_active:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        user = self.get_user(user_id)
        if not user:
            raise KeyError(user_id)
        return user

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (token_hash, user_id, expires_at.isoformat()))

    def user_for_session(self, token_hash: str) -> User | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1",
                (token_hash, _now()),
            ).fetchone()
        return _user(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def create_store(self, *, store_id: str, name: str, region_id: str) -> Store:
        with self.connect() as db:
            db.execute("INSERT INTO stores VALUES (?, ?, ?, 1, ?)",
                       (store_id, name, region_id, _now()))
        return self.get_store(store_id)  # type: ignore[return-value]

    def get_store(self, store_id: str) -> Store | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
        return _store(row) if row else None

    def list_stores(self) -> list[Store]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM stores ORDER BY region_id, name").fetchall()
        return [_store(row) for row in rows]

    def create_media(self, item: MediaItem) -> MediaItem:
        with self.connect() as db:
            db.execute(
                "INSERT INTO media_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item.id, item.store_id, item.title, item.media_type.value, item.content_type,
                 item.original_name, item.storage_name, item.image_duration_seconds,
                 item.sort_order, int(item.is_published), item.created_by,
                 item.created_at.isoformat(), item.updated_at.isoformat()),
            )
        return item

    def get_media(self, media_id: str) -> MediaItem | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM media_items WHERE id = ?", (media_id,)).fetchone()
        return _media(row) if row else None

    def list_media(self, store_id: str, *, published_only: bool = False) -> list[MediaItem]:
        query = "SELECT * FROM media_items WHERE store_id = ?"
        if published_only:
            query += " AND is_published = 1"
        query += " ORDER BY sort_order, created_at"
        with self.connect() as db:
            rows = db.execute(query, (store_id,)).fetchall()
        return [_media(row) for row in rows]

    def update_media(self, media_id: str, *, title: str, image_duration_seconds: float | None,
                     sort_order: int, is_published: bool) -> MediaItem:
        with self.connect() as db:
            db.execute(
                "UPDATE media_items SET title = ?, image_duration_seconds = ?, sort_order = ?, "
                "is_published = ?, updated_at = ? WHERE id = ?",
                (title, image_duration_seconds, sort_order, int(is_published), _now(), media_id),
            )
        item = self.get_media(media_id)
        if not item:
            raise KeyError(media_id)
        return item

    def apply_playlist(self, store_id: str, *, updates: list[dict[str, object]],
                       delete_ids: list[str]) -> list[MediaItem]:
        with self.connect() as db:
            for update in updates:
                cursor = db.execute(
                    "UPDATE media_items SET title = ?, image_duration_seconds = ?, sort_order = ?, "
                    "is_published = ?, updated_at = ? WHERE id = ? AND store_id = ?",
                    (update["title"], update["image_duration_seconds"], update["sort_order"],
                     int(bool(update["is_published"])), _now(), update["id"], store_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(str(update["id"]))
            for media_id in delete_ids:
                cursor = db.execute(
                    "DELETE FROM media_items WHERE id = ? AND store_id = ?", (media_id, store_id)
                )
                if cursor.rowcount != 1:
                    raise KeyError(media_id)
        return self.list_media(store_id)

    def delete_media(self, media_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM media_items WHERE id = ?", (media_id,))


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["display_name"], Role(row["role"]),
                row["region_id"], row["store_id"], bool(row["is_active"]),
                datetime.fromisoformat(row["created_at"]))


def _store(row: sqlite3.Row) -> Store:
    return Store(row["id"], row["name"], row["region_id"], bool(row["is_active"]),
                 datetime.fromisoformat(row["created_at"]))


def _media(row: sqlite3.Row) -> MediaItem:
    return MediaItem(
        row["id"], row["store_id"], row["title"], MediaType(row["media_type"]),
        row["content_type"], row["original_name"], row["storage_name"],
        row["image_duration_seconds"], row["sort_order"], bool(row["is_published"]),
        row["created_by"], datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["updated_at"]),
    )
