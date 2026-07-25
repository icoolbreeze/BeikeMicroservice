"""OCR Provider 实现占位。

当前不接入任何真实 OCR 引擎。
"""
from __future__ import annotations


class LocalOCRProvider:
    """OCR 引擎适配（当前不实现）。

    TODO: 实现 domain.providers.ocr_provider.OCRProvider。
    """

    def extract_text(self, image_ref: str) -> str:
        raise NotImplementedError
