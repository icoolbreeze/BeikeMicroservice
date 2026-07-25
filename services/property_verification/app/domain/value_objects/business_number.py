"""业务件号值对象（占位）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessNumber:
    """业务件号。格式校验规则待定义。"""

    value: str

    def __post_init__(self) -> None:
        # TODO: 定义并执行格式校验。
        pass
