"""验证任务仓储接口（领域层抽象，占位）。

由 infrastructure.database.repository 实现；当前不创建任何数据库表。
"""
from abc import ABC, abstractmethod


class VerificationRepository(ABC):
    """验证任务持久化抽象。"""

    @abstractmethod
    def save(self, job) -> None:
        """保存任务（占位）。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, job_id: str):
        """按 ID 查询任务，不存在时返回 None（占位）。"""
        raise NotImplementedError
