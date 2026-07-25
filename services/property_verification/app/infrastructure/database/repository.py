"""验证任务仓储实现（占位）。

实现 domain.repositories.verification_repository.VerificationRepository。
"""
from __future__ import annotations


class SqlVerificationRepository:
    """基于关系数据库的验证任务仓储（当前不实现）。

    TODO: 继承 VerificationRepository，注入数据库会话实现 save/get。
    """

    def save(self, job) -> None:
        raise NotImplementedError

    def get(self, job_id: str):
        raise NotImplementedError
