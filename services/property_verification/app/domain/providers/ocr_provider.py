"""OCR 能力抽象（占位）。

domain 只定义接口，具体引擎接入由 infrastructure.ocr 实现。
"""
from abc import ABC, abstractmethod


class OCRProvider(ABC):
    """从证件图片中提取文本的能力抽象。"""

    @abstractmethod
    def extract_text(self, image_ref: str) -> str:
        """提取图片文本（占位）。"""
        raise NotImplementedError
