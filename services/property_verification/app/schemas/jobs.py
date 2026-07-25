"""任务查询相关 API 结构（占位）。"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobResult:
    """任务状态与结果（占位）。

    data 中的敏感字段必须脱敏后返回。
    """

    job_id: str
    status: str
    data: Any = None
