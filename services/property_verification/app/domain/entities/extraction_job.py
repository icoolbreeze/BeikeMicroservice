"""证件信息提取任务实体（占位）。

仅定义结构，不包含任何行为实现。
"""
from dataclasses import dataclass
from enum import Enum


class ExtractionStatus(str, Enum):
    """提取任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ExtractionJob:
    """提取任务聚合。

    business_number / certificate_number：OCR 提取出的业务件号与证件编码
    （提取完成前为 None）。
    """

    job_id: str
    image_ref: str
    status: ExtractionStatus = ExtractionStatus.PENDING
    business_number: str | None = None
    certificate_number: str | None = None
