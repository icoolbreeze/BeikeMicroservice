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
    roughcast_cache_seconds: int = 60
    # 清水房全量采集（队列 A）。默认关闭:先用手动入口盯着跑几天,确认流量曲线
    # 符合 docs/roughcast-quality-ranking.md 第三章之后再打开常驻线程。
    roughcast_crawl_enabled: bool = False
    roughcast_crawl_page_size: int = 50
    roughcast_daily_request_cap: int = 260          # 硬顶,任何推导值都不得越过
    roughcast_crawl_safety_factor: float = 1.15
    roughcast_crawl_retry_reserve: int = 10
    roughcast_min_request_interval_seconds: float = 20.0
    roughcast_page_gap_seconds: tuple[float, float] = (25.0, 90.0)
    roughcast_long_pause_every_pages: tuple[int, int] = (8, 15)
    roughcast_long_pause_seconds: tuple[float, float] = (180.0, 480.0)
    roughcast_crawl_window: tuple[str, str] = ("09:30", "19:00")
    roughcast_crawl_start_jitter_minutes: int = 40
    roughcast_crawl_utc_offset_hours: int = 8        # Asia/Shanghai,无夏令时

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "store_media.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def featured_snapshot_path(self) -> Path:
        return self.storage_dir / "featured_snapshot.json"


def _pair(name: str, default: tuple[float, float]) -> tuple[float, float]:
    """读一个 `min,max` 形式的环境变量。写反了直接报错,不静默交换。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(parts) != 2:
        raise ValueError(f"{name} 需要 'min,max' 两个值,收到 {raw!r}")
    low, high = float(parts[0]), float(parts[1])
    if low > high:
        raise ValueError(f"{name} 的下界大于上界:{raw!r}")
    return (low, high)


def _int_pair(name: str, default: tuple[int, int]) -> tuple[int, int]:
    low, high = _pair(name, (float(default[0]), float(default[1])))
    return (int(low), int(high))


def _window(name: str, default: tuple[str, str]) -> tuple[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    parts = [part.strip() for part in raw.split("-") if part.strip()]
    if len(parts) != 2:
        raise ValueError(f"{name} 需要 'HH:MM-HH:MM' 形式,收到 {raw!r}")
    return (parts[0], parts[1])


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
        roughcast_cache_seconds=int(os.getenv("SM_ROUGHCAST_CACHE_SECONDS", "60")),
        roughcast_crawl_enabled=os.getenv("SM_ROUGHCAST_CRAWL_ENABLED", "0").strip() in
        {"1", "true", "True", "yes", "on"},
        roughcast_crawl_page_size=int(os.getenv("SM_ROUGHCAST_CRAWL_PAGE_SIZE", "50")),
        roughcast_daily_request_cap=int(os.getenv("SM_ROUGHCAST_DAILY_REQUEST_CAP", "260")),
        roughcast_crawl_safety_factor=float(
            os.getenv("SM_ROUGHCAST_CRAWL_SAFETY_FACTOR", "1.15")
        ),
        roughcast_crawl_retry_reserve=int(os.getenv("SM_ROUGHCAST_CRAWL_RETRY_RESERVE", "10")),
        roughcast_min_request_interval_seconds=float(
            os.getenv("SM_ROUGHCAST_MIN_REQUEST_INTERVAL_SECONDS", "20")
        ),
        roughcast_page_gap_seconds=_pair("SM_ROUGHCAST_PAGE_GAP_SECONDS", (25.0, 90.0)),
        roughcast_long_pause_every_pages=_int_pair(
            "SM_ROUGHCAST_LONG_PAUSE_EVERY_PAGES", (8, 15)
        ),
        roughcast_long_pause_seconds=_pair("SM_ROUGHCAST_LONG_PAUSE_SECONDS", (180.0, 480.0)),
        roughcast_crawl_window=_window("SM_ROUGHCAST_CRAWL_WINDOW", ("09:30", "19:00")),
        roughcast_crawl_start_jitter_minutes=int(
            os.getenv("SM_ROUGHCAST_CRAWL_START_JITTER_MINUTES", "40")
        ),
        roughcast_crawl_utc_offset_hours=int(
            os.getenv("SM_ROUGHCAST_CRAWL_UTC_OFFSET_HOURS", "8")
        ),
    )
