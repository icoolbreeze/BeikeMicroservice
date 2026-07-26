"""通用 API 结构：统一响应信封。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """统一响应结构。"""

    code: int = 0
    message: str = "ok"
    data: Optional[Any] = None


class ErrorDetail(BaseModel):
    """错误详情。"""

    code: int
    message: str
    retry_after: int = 0
    detail: Optional[str] = None
