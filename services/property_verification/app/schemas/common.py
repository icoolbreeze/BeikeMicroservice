"""通用 API 结构（占位）。

统一响应信封约定见 docs/api-conventions.md。
TODO: 迁移到 Pydantic 模型。
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    """统一响应结构（占位）。"""

    code: int = 0
    message: str = "ok"
    data: Any = None
