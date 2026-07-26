"""健康检查端点。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """服务存活与版本信息。"""
    s = get_settings()
    return {"status": "ok", "service": "property_verification",
            "env": s.app_env}
