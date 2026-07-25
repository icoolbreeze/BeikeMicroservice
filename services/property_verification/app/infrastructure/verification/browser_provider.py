"""浏览器自动化验证渠道（占位）。

未来通过合法授权的浏览器自动化方式访问验证渠道。
当前：不实现 Playwright、不访问任何网站、不分析任何网站接口。
"""
from __future__ import annotations


class BrowserVerificationProvider:
    """浏览器自动化验证渠道适配（当前不实现）。

    TODO: 实现 domain.providers.verification_provider.VerificationProvider。
    """

    def verify(self, business_number: str, certificate_number: str):
        raise NotImplementedError
