"""服务间事件结构（占位）。

当前不实现任何事件发布/订阅机制，仅定义基础结构。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """领域事件基础结构。

    name: 事件名（如 service.entity.action）；payload: 事件数据。
    TODO: 补充 occurred_at、event_id、版本字段。
    """

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
