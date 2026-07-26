"""房源信息验证相关 API 结构。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JobCreatedView(BaseModel):
    """任务创建响应。"""

    job_id: str
    status: str
    remaining_minute: int
    remaining_day: int


class ArtifactView(BaseModel):
    """可下载产物。"""

    spec: str
    title: str
    filename: str
    size: int
    content_type: str
    url: str


class EventView(BaseModel):
    """进度事件（SSE 推送）。"""

    ts: float
    type: str
    message: str


class JobStatusView(BaseModel):
    """任务状态视图。"""

    job_id: str
    status: str
    created_at: float
    finished_at: Optional[float]
    error: Optional[str]
    artifacts: list[ArtifactView]
