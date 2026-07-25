"""敏感信息脱敏（占位）。

用于日志与接口响应中脱敏证件编码、业务件号等字段。
"""
from __future__ import annotations


def mask_certificate_number(value: str) -> str:
    """证件编码脱敏（占位）。

    TODO: 定义脱敏规则（如保留首尾、中间打码）。
    """
    raise NotImplementedError


def mask_business_number(value: str) -> str:
    """业务件号脱敏（占位）。"""
    raise NotImplementedError
