"""API 依赖注入：配置、应用服务、任务存储等单例的装配入口。"""
from __future__ import annotations

from functools import lru_cache

from app.application.services.verification_service import VerificationService
from app.infrastructure.config.settings import Settings, load_settings
from app.infrastructure.job_store import JobStore, get_store


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取服务配置单例。"""
    return load_settings()


@lru_cache(maxsize=1)
def get_job_store() -> JobStore:
    """获取任务存储单例。"""
    return get_store()


@lru_cache(maxsize=1)
def get_verification_service() -> VerificationService:
    """获取验证应用服务单例（含限流器与线程池）。"""
    return VerificationService(get_settings(), get_job_store())


def get_client_ip(request) -> str:
    """取真实客户端 IP（兼容反代 X-Forwarded-For）。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
