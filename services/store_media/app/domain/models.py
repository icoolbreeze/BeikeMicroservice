from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    SYSTEM_ADMIN = "system_admin"
    REGIONAL_MANAGER = "regional_manager"
    STORE_MANAGER = "store_manager"
    STAFF = "staff"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: Role
    region_id: str | None
    store_id: str | None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    region_id: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class MediaItem:
    id: str
    store_id: str
    title: str
    media_type: MediaType
    content_type: str
    original_name: str
    storage_name: str
    image_duration_seconds: float | None
    sort_order: int
    is_published: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
