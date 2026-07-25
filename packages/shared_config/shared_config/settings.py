"""公共配置加载入口（占位）。

约定：每个服务拥有自己的 Settings 类，统一从环境变量读取；
本模块只提供通用加载入口，不定义任何业务配置字段。
"""
from __future__ import annotations

import os


def get_env(key: str, default: str = "") -> str:
    """读取环境变量（占位）。"""
    return os.environ.get(key, default)


def load_settings(settings_cls):
    """按约定构造服务配置（占位）。

    TODO: 统一约定各服务 Settings 的加载方式（如 pydantic-settings）。
    """
    raise NotImplementedError
