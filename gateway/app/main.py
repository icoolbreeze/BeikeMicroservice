"""API 网关入口（占位）。

未来职责：统一入口、路由转发、身份验证、限流、服务发现。
当前阶段不实现任何转发或鉴权逻辑。
"""
from __future__ import annotations


def create_app():
    """创建网关应用实例（占位）。

    TODO: 接入 FastAPI，按 app.routes 中的路由注册表挂载转发规则。
    """
    raise NotImplementedError("网关应用尚未实现")
