"""后台任务注册表（占位）。

仅维护任务名称到处理函数的映射结构；当前不注册、不执行任何真实任务。
"""
from __future__ import annotations

from typing import Callable

# 任务名称 -> 处理函数
TASK_REGISTRY: dict[str, Callable[..., object]] = {}


def register(name: str):
    """任务注册装饰器（占位）。"""

    def decorator(func: Callable[..., object]) -> Callable[..., object]:
        TASK_REGISTRY[name] = func
        return func

    return decorator


def get_task(name: str) -> Callable[..., object] | None:
    """按名称获取任务处理函数。"""
    return TASK_REGISTRY.get(name)
