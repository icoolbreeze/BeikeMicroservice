"""房源信息验证相关 API 结构（占位）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationJobCreated:
    """验证任务创建响应（占位）。"""

    job_id: str


@dataclass(frozen=True)
class VerificationResultView:
    """验证结果视图（占位）。

    注意：返回前敏感字段必须脱敏（security.masking）。
    TODO: 定义结果字段（校验状态、房源摘要、截图引用等）。
    """

    job_id: str
    status: str
