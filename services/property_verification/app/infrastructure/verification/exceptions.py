"""验证渠道相关异常定义（仅类型定义，无逻辑）。"""


class VerificationChannelError(Exception):
    """验证渠道通用错误。"""


class VerificationTimeoutError(VerificationChannelError):
    """渠道访问超时。"""


class VerificationRejectedError(VerificationChannelError):
    """渠道拒绝请求（如参数无效、凭据失效）。"""


class ResultParseError(VerificationChannelError):
    """验证结果解析失败。"""
