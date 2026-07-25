"""审计记录（占位）。

关键动作（提取、验证、导出）需记录审计事件；审计内容需脱敏。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEvent:
    """审计事件结构（占位）。

    TODO: 补充时间、来源 IP 等字段；落地方式（日志/库表）待定。
    """

    actor: str
    action: str
    detail: str = ""


def record_audit(event: AuditEvent) -> None:
    """记录审计事件（占位）。"""
    raise NotImplementedError
