"""房源信息验证渠道抽象（占位）。

仅允许对接合法授权的验证渠道（见 docs/security.md）；
具体渠道接入由 infrastructure.verification 实现。
"""
from abc import ABC, abstractmethod


class VerificationProvider(ABC):
    """房源信息验证渠道的能力抽象。"""

    @abstractmethod
    def verify(self, business_number: str, certificate_number: str):
        """发起验证并返回原始结果（占位）。

        TODO: 定义原始结果结构（含页面数据与截图引用）。
        """
        raise NotImplementedError
