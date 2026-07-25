"""网关配置（占位）。

从环境变量读取；禁止写入真实地址与凭据。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewaySettings:
    """网关配置结构。

    TODO: 补充上游服务地址表、限流参数等（全部经环境变量注入）。
    """

    app_env: str = "dev"
    log_level: str = "INFO"
