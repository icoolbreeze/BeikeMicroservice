"""证件信息提取端点（占位）。

规划接口：
- POST /extraction/jobs：上传不动产权证图片并创建提取任务，返回 job_id。
"""
from __future__ import annotations


async def create_extraction_job():
    """创建提取任务（占位）。

    TODO: 接收上传文件 -> 安全校验（security.file_validation）->
    存入 storage/uploads -> 构造 ExtractCertificateCommand 交由应用服务。
    """
    raise NotImplementedError
