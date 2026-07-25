"""服务入口：组装 FastAPI 应用、挂载路由、注册生命周期钩子。

当前仅提供应用工厂占位，不包含任何业务实现。
"""
from __future__ import annotations


def create_app():
    """应用工厂（占位）。

    TODO: 创建 FastAPI 实例，挂载 app.api.router 聚合的 v1 路由，
    注册启动/关闭钩子（配置加载、存储目录初始化等）。
    """
    raise NotImplementedError("应用工厂尚未实现")
