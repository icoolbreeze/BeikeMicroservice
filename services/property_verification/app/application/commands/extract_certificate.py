"""提取不动产权证信息命令（占位）。

仅承载输入数据，不包含任何业务逻辑。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractCertificateCommand:
    """创建证件信息提取任务所需的输入。

    image_ref: 图片在存储中的引用（非图片内容本身）。
    requester: 发起方标识（用于审计）。
    """

    image_ref: str
    requester: str = ""
