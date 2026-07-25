"""公共标识符类型与生成接口（占位）。"""
from typing import NewType

JobId = NewType("JobId", str)
ServiceName = NewType("ServiceName", str)


def new_id() -> str:
    """生成全局唯一 ID（占位）。

    TODO: 选择实现方案（uuid4 / ulid 等）。
    """
    raise NotImplementedError
