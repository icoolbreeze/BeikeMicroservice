"""房源信息验证应用服务（占位）。

职责（编排）：接收命令 -> 创建验证任务 -> 调用合法授权验证渠道 ->
解析结果 -> 生成查询截图 -> 持久化。
"""
from __future__ import annotations


class VerificationService:
    """房源信息验证用例编排（当前不实现）。"""

    def submit(self, command) -> str:
        """提交验证任务，返回任务 ID（占位）。"""
        raise NotImplementedError

    def get_result(self, query):
        """查询验证结果（占位，返回内容需脱敏）。"""
        raise NotImplementedError
