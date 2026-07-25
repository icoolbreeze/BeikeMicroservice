"""公共基础异常定义。

所有服务可继承这些异常；禁止在此加入任何具体业务异常。
"""


class BeikeServiceError(Exception):
    """平台基础异常。"""


class DomainError(BeikeServiceError):
    """领域层错误。"""


class ApplicationError(BeikeServiceError):
    """应用层错误。"""


class InfrastructureError(BeikeServiceError):
    """基础设施错误（网络、存储、外部依赖等）。"""
