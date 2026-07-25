"""服务配置定义（占位）。

所有配置从环境变量读取；禁止在代码中写入真实地址、账号或密钥。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """服务配置结构。

    TODO: 迁移到 pydantic-settings 并补充字段校验。
    """

    app_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = ""
    redis_url: str = ""
    storage_dir: str = "./storage"
    ocr_provider: str = ""
    verification_provider: str = ""
    verification_channel_url: str = ""
    verification_channel_token: str = ""


def load_settings() -> Settings:
    """从环境变量加载配置（占位）。"""
    raise NotImplementedError
