"""通用结果类型（占位）。

用于在应用层显式表达成功/失败，避免以异常控制正常流程。
"""
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    """操作结果：ok 为 True 时 value 有效，否则 error 含错误说明。"""

    ok: bool
    value: T | None = None
    error: str | None = None
