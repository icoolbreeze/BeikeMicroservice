"""任务队列（占位）。

实现 domain.providers.task_queue_provider.TaskQueueProvider；
 broker 选型（Redis / 其他）待定，当前不实现。
"""
from __future__ import annotations


class TaskQueue:
    """任务队列适配（当前不实现）。"""

    def enqueue(self, task_name: str, payload: dict) -> str:
        raise NotImplementedError
