"""API 依赖注入占位：配置、仓储、外部 Provider 等的装配入口。"""
from __future__ import annotations


def get_settings():
    """获取服务配置（占位）。

    TODO: 返回 infrastructure.config.settings.Settings 实例。
    """
    raise NotImplementedError


def get_verification_repository():
    """获取验证任务仓储（占位）。

    TODO: 返回 domain.repositories.verification_repository 的实现。
    """
    raise NotImplementedError


def get_ocr_provider():
    """获取 OCR Provider（占位）。

    TODO: 按配置返回 domain.providers.ocr_provider.OCRProvider 的实现。
    """
    raise NotImplementedError
