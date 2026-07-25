"""任务结果查询端点（占位）。

规划接口：
- GET /jobs/{job_id}：查询提取/验证任务状态与结果。
"""
from __future__ import annotations


async def get_job(job_id: str):
    """查询任务（占位）。

    TODO: 通过 GetJobResultQuery 查询任务，返回脱敏后的结果。
    """
    raise NotImplementedError
