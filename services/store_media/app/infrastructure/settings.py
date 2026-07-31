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

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "store_media.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"


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
    )
