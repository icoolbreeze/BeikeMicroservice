from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    max_upload_mb: int = 500
    session_hours: int = 12
    cors_origins: tuple[str, ...] = ()
    bootstrap_admin_username: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_display_name: str = "系统管理员"
    news_feed_url: str = (
        "https://www.cdfangxie.com/Infor/type/typeid/22.html,"
        "http://www.news.cn/house/ds_ae1f7b9d03624853bc927f6c216be3b3.json"
    )
    news_cache_seconds: int = 1800
    news_feed_size: int = 20
    weather_latitude: float = 30.66
    weather_longitude: float = 104.10
    weather_location_name: str = "成都 · 成华"
    weather_cache_seconds: int = 600
    crm_connector_base_url: str = "http://127.0.0.1:8020"
    featured_cache_seconds: int = 600

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "store_media.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def featured_snapshot_path(self) -> Path:
        return self.storage_dir / "featured_snapshot.json"


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    configured_storage = os.getenv("SM_STORAGE_DIR", "").strip()
    return Settings(
        storage_dir=Path(configured_storage) if configured_storage else root / "storage",
        max_upload_mb=int(os.getenv("SM_MAX_UPLOAD_MB", "500")),
        session_hours=int(os.getenv("SM_SESSION_HOURS", "12")),
        cors_origins=tuple(
            origin.strip() for origin in os.getenv("SM_CORS_ORIGINS", "").split(",")
            if origin.strip()
        ),
        bootstrap_admin_username=os.getenv("SM_BOOTSTRAP_ADMIN_USERNAME", "").strip(),
        bootstrap_admin_password=os.getenv("SM_BOOTSTRAP_ADMIN_PASSWORD", ""),
        bootstrap_admin_display_name=os.getenv("SM_BOOTSTRAP_ADMIN_DISPLAY_NAME", "系统管理员").strip(),
        news_feed_url=os.getenv("SM_NEWS_FEED_URL", Settings.news_feed_url).strip(),
        news_cache_seconds=int(os.getenv("SM_NEWS_CACHE_SECONDS", "1800")),
        news_feed_size=int(os.getenv("SM_NEWS_FEED_SIZE", "20")),
        weather_latitude=float(os.getenv("SM_WEATHER_LAT", "30.66")),
        weather_longitude=float(os.getenv("SM_WEATHER_LON", "104.10")),
        weather_location_name=os.getenv("SM_WEATHER_LOCATION", "成都 · 成华").strip(),
        weather_cache_seconds=int(os.getenv("SM_WEATHER_CACHE_SECONDS", "600")),
        crm_connector_base_url=os.getenv("SM_CRM_CONNECTOR_BASE_URL", "http://127.0.0.1:8020").strip(),
        featured_cache_seconds=int(os.getenv("SM_FEATURED_CACHE_SECONDS", "120")),
    )
