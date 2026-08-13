"""服务配置：从环境变量加载运行参数。

所有配置均可经环境变量覆盖；禁止在代码中写入真实地址、账号或密钥。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 加载服务目录下的 .env（存在时），使 README 指引生效
_SERVICE_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_SERVICE_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """服务运行配置。"""

    app_env: str = "dev"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # 本地文件存储目录（位于服务 storage/ 下）
    storage_dir: str = ""

    # 视觉模型（顺序主备：前一个失败重试后仍失败，由下一个接管）
    # 主模型走 OpenRouter；兜底模型走 NVIDIA build.nvidia.com。
    openrouter_api_key: str = ""
    nvidia_api_key: str = ""
    vl_model: str = "nvidia/nemotron-nano-12b-v2-vl:free"
    vl_model_fallback: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    # 第二兜底（可选）：留空则不启用
    vl_model_fallback2: str = "stepfun-ai/step-3.7-flash"
    # 每个模型失败（连接异常或未识别出有效字段）后的重试次数
    vl_retries: int = 2
    # 单次模型调用的超时（秒）
    vl_timeout: float = 30.0
    # 模型健康探活间隔（分钟）；不可用模型在提取链中直接跳过
    model_health_interval_minutes: int = 60

    # 验证渠道页面地址（合法授权来源）
    verification_channel_url: str = ""

    # 限流：同一 IP 每分钟请求数上限、每日请求数上限
    rate_per_minute: int = 2
    rate_per_day: int = 30

    # 视觉模型账户级预算（OpenRouter 与 NVIDIA 各自独立；按实际模型请求计数）
    model_rate_per_minute: int = 15
    model_rate_per_day: int = 800

    # 上传限制
    max_upload_mb: int = 10

    # 产物保留与短期链接
    # 任务产物（截图等）保留天数，超过后由后台清理任务删除
    artifact_retention_days: int = 7
    # 短期签名链接的最大有效期（秒）
    download_token_ttl_seconds: int = 600
    # 签名密钥；留空则每次进程启动随机生成（链接随服务重启失效）
    download_token_secret: str = ""

    # 并发浏览器会话上限与等待队列上限（等待数不含正在运行的任务）。
    max_concurrent_jobs: int = 3
    max_queued_jobs: int = 12

    # 仓库 scripts 目录（复用已有验证逻辑），留空则按本文件位置推断
    scripts_dir: str = ""

    # MCP（pv-mcp）：指向已部署服务的 HTTP 客户端
    pv_base_url: str = "http://127.0.0.1:8000"
    # 提交核验的本地限流（与服务端 2 次/分钟对齐）
    mcp_rate_limit_per_min: int = 2
    # 状态轮询/产物下载等只读操作的本地限流
    mcp_poll_rate_limit_per_min: int = 60
    # 产物下载目录（留空则用系统临时目录）
    mcp_download_dir: str = ""

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
        nvidia_api_key=_env("NVIDIA_API_KEY"),
        vl_model=_env("PV_VL_MODEL", "nvidia/nemotron-nano-12b-v2-vl:free"),
        vl_model_fallback=_env(
            "PV_VL_MODEL_FALLBACK",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
        vl_model_fallback2=_env(
            "PV_VL_MODEL_FALLBACK2", "stepfun-ai/step-3.7-flash"),
        vl_retries=int(_env("PV_VL_RETRIES", "2") or 2),
        vl_timeout=float(_env("PV_VL_TIMEOUT", "30") or 30),
        model_health_interval_minutes=int(
            _env("PV_MODEL_HEALTH_INTERVAL_MINUTES", "60") or 60),
        verification_channel_url=_env(
            "PV_VERIFICATION_URL",
            "https://blmp.cdzjryb.com/fplc_daas_portal/#/integratedQueryNew"
            "?prevPageTitle=%E4%BD%8F%E5%BB%BA%E8%93%89e%E5%8A%9E&code=50",
        ),
        rate_per_minute=int(_env("PV_RATE_PER_MIN", "2") or 2),
        rate_per_day=int(_env("PV_RATE_PER_DAY", "30") or 30),
        model_rate_per_minute=int(_env("PV_MODEL_RATE_PER_MIN", "15") or 15),
        model_rate_per_day=int(_env("PV_MODEL_RATE_PER_DAY", "800") or 800),
        max_upload_mb=int(_env("PV_MAX_UPLOAD_MB", "10") or 10),
        artifact_retention_days=int(_env("PV_ARTIFACT_RETENTION_DAYS", "7") or 7),
        download_token_ttl_seconds=int(_env("PV_DOWNLOAD_TOKEN_TTL", "600") or 600),
        download_token_secret=_env("PV_DOWNLOAD_TOKEN_SECRET"),
        max_concurrent_jobs=int(_env("PV_MAX_CONCURRENT", "3") or 3),
        max_queued_jobs=int(_env("PV_MAX_QUEUED", "12") or 12),
        scripts_dir=_env("PV_SCRIPTS_DIR"),
        pv_base_url=_env("PV_BASE_URL", "http://127.0.0.1:8000"),
        mcp_rate_limit_per_min=int(_env("PV_MCP_RATE_PER_MIN", "2") or 2),
        mcp_poll_rate_limit_per_min=int(_env("PV_MCP_POLL_RATE_PER_MIN", "60") or 60),
        mcp_download_dir=_env("PV_MCP_DOWNLOAD_DIR"),
    )
