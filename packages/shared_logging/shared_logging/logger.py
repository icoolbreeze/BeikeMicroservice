"""统一日志入口（占位）。

规划：结构化（JSON）日志格式，含服务名、请求 ID、时间戳等公共字段。
"""
from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """获取按统一格式配置的 logger（占位）。

    TODO: 配置结构化格式与脱敏过滤器，当前返回标准 logger。
    """
    return logging.getLogger(name)
