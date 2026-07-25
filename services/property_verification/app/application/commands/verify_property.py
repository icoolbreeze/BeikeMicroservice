"""房源信息验证命令（占位）。

仅承载输入数据，不包含任何校验或查询逻辑。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class VerifyPropertyCommand:
    """发起房源信息验证的输入。

    business_number: 业务件号；certificate_number: 证件编码。
    """

    business_number: str
    certificate_number: str
