"""运行环境枚举。"""

from enum import Enum


class Environment(str, Enum):
    """部署环境。"""

    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"
