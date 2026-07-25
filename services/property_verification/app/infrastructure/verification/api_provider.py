"""API 验证渠道（占位）。

未来对接合法授权的 HTTP API 验证渠道。
当前不实现任何 HTTP 调用。
"""
from __future__ import annotations


class ApiVerificationProvider:
    """HTTP API 验证渠道适配（当前不实现）。

    TODO: 实现 domain.providers.verification_provider.VerificationProvider。
    """

    def verify(self, business_number: str, certificate_number: str):
        raise NotImplementedError
