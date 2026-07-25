"""后台任务 Worker（占位）。

消费队列中的提取/验证任务并调用应用服务；当前不实现任务执行。
"""
from __future__ import annotations


class Worker:
    """后台任务消费者（当前不实现）。

    TODO: 注册任务处理器、拉取任务、失败重试与死信处理。
    """

    def run(self) -> None:
        raise NotImplementedError
