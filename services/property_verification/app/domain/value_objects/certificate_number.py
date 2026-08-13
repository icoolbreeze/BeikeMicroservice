"""证件编码值对象。

格式（宽容校验，新旧版证件）：
- 新版：省（年份）市不动产产权第N号，如 川（2025）成都市不动产权第0373259号
- 旧版：「监证」开头编号，如 监证1234567

与 scripts/house_verify.py 中的提取期校验保持一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CERTIFICATE_NUMBER_PATTERNS = (
    re.compile(r"^监证\d+$"),
    re.compile(r"^.*第\d+号$"),
)


def is_valid_certificate_number(value: str) -> bool:
    """宽容校验证件编码（新旧版格式）。"""
    return any(p.fullmatch(value or "") for p in _CERTIFICATE_NUMBER_PATTERNS)


@dataclass(frozen=True)
class CertificateNumber:
    """不动产权证证件编码。"""

    value: str

    def __post_init__(self) -> None:
        if not is_valid_certificate_number(self.value):
            raise ValueError(
                f"证件编码格式不符：{self.value!r}（应为 监证编号 或 …第N号）")
