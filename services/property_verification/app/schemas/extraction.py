"""证件信息提取相关 API 结构（占位）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionJobCreated:
    """提取任务创建响应（占位）。

    TODO: 迁移到 Pydantic，并补充任务状态字段。
    """

    job_id: str
