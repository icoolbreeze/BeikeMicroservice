"""证件字段提取（占位）。

从 OCR 文本中定位业务件号、证件编码等字段（规则/模型待选型）。
当前不实现任何提取逻辑。
"""
from __future__ import annotations


def extract_fields(text: str) -> dict:
    """从 OCR 文本提取字段（占位）。

    TODO: 定义返回结构，如 {"business_number": ..., "certificate_number": ...}。
    """
    raise NotImplementedError
