"""证件信息提取应用服务（占位）。

职责（编排，不含领域规则与外部调用细节）：
接收命令 -> 创建提取任务实体 -> 调度 OCR Provider -> 持久化 -> 返回任务标识。
"""
from __future__ import annotations


class ExtractionService:
    """证件信息提取用例编排（当前不实现）。"""

    def submit(self, command) -> str:
        """提交提取任务，返回任务 ID（占位）。

        TODO: 创建 ExtractionJob，经 TaskQueueProvider 派发后台提取。
        """
        raise NotImplementedError
