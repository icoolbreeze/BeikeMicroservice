"""任务结果查询（占位）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GetJobResultQuery:
    """按任务 ID 查询任务状态与结果。"""

    job_id: str
