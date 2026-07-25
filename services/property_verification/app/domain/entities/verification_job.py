"""房源信息验证任务实体（占位）。

仅定义结构，不包含任何行为实现。
"""
from dataclasses import dataclass
from enum import Enum


class VerificationStatus(str, Enum):
    """验证任务状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class VerificationJob:
    """验证任务聚合。

    result_ref：验证结果引用；screenshot_ref：查询结果截图引用（完成前为 None）。
    """

    job_id: str
    business_number: str
    certificate_number: str
    status: VerificationStatus = VerificationStatus.PENDING
    result_ref: str | None = None
    screenshot_ref: str | None = None
