"""服务间消息信封（占位）。

当前不实现任何消息队列通信，仅定义基础结构。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Message:
    """消息信封：topic 标识通道，payload 承载数据。

    TODO: 补充 message_id、trace_id、时间戳字段。
    """

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
