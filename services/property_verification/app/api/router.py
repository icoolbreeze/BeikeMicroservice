"""API 路由聚合：将 v1 各端点路由挂载到 /api/v1 前缀下。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import health, jobs, verification

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(verification.router)
api_router.include_router(jobs.router)
