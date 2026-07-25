"""任务队列抽象（占位）。"""
from abc import ABC, abstractmethod


class TaskQueueProvider(ABC):
    """后台任务派发能力抽象。"""

    @abstractmethod
    def enqueue(self, task_name: str, payload: dict) -> str:
        """派发任务并返回队列内标识（占位）。"""
        raise NotImplementedError
