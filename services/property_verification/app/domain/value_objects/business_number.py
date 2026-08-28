"""业务件号值对象。

格式（宽容校验，新旧版证件）：
- 新版：办理日期 + F + 编号，如 2025112701F90697、2016092042F1234
- 旧版：「权」开头编号，如 权1234

与 scripts/house_verify.py 中的提取期校验保持一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_BUSINESS_NUMBER_PATTERNS = (
    re.compile(r"^\d{6,12}[A-Z]\d{2,10}$"),
    re.compile(r"^权\d+$"),
)


def is_valid_business_number(value: object) -> bool:
    """宽容校验业务件号（新旧版格式）；首尾空白忽略，非字符串视为空。"""
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    return any(p.fullmatch(cleaned) for p in _BUSINESS_NUMBER_PATTERNS)


@dataclass(frozen=True)
class BusinessNumber:
    """业务件号。"""

    value: str

    def __post_init__(self) -> None:
        cleaned = self.value.strip() if isinstance(self.value, str) else ""
        if not is_valid_business_number(cleaned):
            raise ValueError(
                f"业务件号格式不符：{self.value!r}（应为 日期F编号 或 权编号）")
        object.__setattr__(self, "value", cleaned)
