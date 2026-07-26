"""服务配置：从环境变量加载运行参数。

所有配置均可经环境变量覆盖；禁止在代码中写入真实地址、账号或密钥。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """服务运行配置。"""

    app_env: str = "dev"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # 本地文件存储目录（位于服务 storage/ 下）
    storage_dir: str = ""

    # OpenRouter 视觉模型
    openrouter_api_key: str = ""
    vl_model: str = "nvidia/nemotron-nano-12b-v2-vl:free"

    # 验证渠道页面地址（合法授权来源）
    verification_channel_url: str = ""

    # 限流：同一 IP 每分钟请求数上限、每日请求数上限
    rate_per_minute: int = 1
    rate_per_day: int = 10

    # 上传限制
    max_upload_mb: int = 10

    # 并发浏览器会话上限（Playwright 重，建议 1~2）
    max_concurrent_jobs: int = 1

    # 仓库 scripts 目录（复用已有验证逻辑），留空则按本文件位置推断
    scripts_dir: str = ""

    @property
    def jobs_root(self) -> Path:
        """验证任务产物根目录。"""
        base = Path(self.storage_dir) if self.storage_dir else (
            Path(__file__).resolve().parents[2] / "storage")
        p = base / "verify_jobs"
        p.mkdir(parents=True, exist_ok=True)
        return p


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_settings() -> Settings:
    """从环境变量加载配置。"""
    storage = _env("STORAGE_DIR")
    if not storage:
        storage = str(Path(__file__).resolve().parents[2] / "storage")
    return Settings(
        app_env=_env("APP_ENV", "dev"),
        log_level=_env("LOG_LEVEL", "INFO"),
        host=_env("PV_HOST", "0.0.0.0"),
        port=int(_env("PV_PORT", "8000") or 8000),
        storage_dir=storage,
        openrouter_api_key=_env("OPENROUTER_API_KEY"),
        vl_model=_env("PV_VL_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free"),
        verification_channel_url=_env(
            "PV_VERIFICATION_URL",
            "https://blmp.cdzjryb.com/fplc_daas_portal/#/integratedQueryNew"
            "?prevPageTitle=%E4%BD%8F%E5%BB%BA%E8%93%89e%E5%8A%9E&code=50",
        ),
        rate_per_minute=int(_env("PV_RATE_PER_MIN", "1") or 1),
        rate_per_day=int(_env("PV_RATE_PER_DAY", "10") or 10),
        max_upload_mb=int(_env("PV_MAX_UPLOAD_MB", "10") or 10),
        max_concurrent_jobs=int(_env("PV_MAX_CONCURRENT", "1") or 1),
        scripts_dir=_env("PV_SCRIPTS_DIR"),
    )
