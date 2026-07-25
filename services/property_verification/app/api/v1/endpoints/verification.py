"""房源信息验证端点（占位）。

规划接口：
- POST /verification/jobs：以业务件号 + 证件编码发起验证任务，返回 job_id。
"""
from __future__ import annotations


async def create_verification_job():
    """创建验证任务（占位）。

    TODO: 校验入参 -> 构造 VerifyPropertyCommand 交由应用服务。
    验证渠道仅允许合法授权来源（见 docs/security.md）。
    """
    raise NotImplementedError
