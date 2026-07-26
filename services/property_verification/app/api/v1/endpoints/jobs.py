"""任务状态查询端点。

接口：
- GET /jobs/{job_id}：查询验证任务状态与产物清单（快照，配合 SSE 使用）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_verification_service
from app.application.services.verification_service import VerificationService
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=ApiResponse)
def get_job(job_id: str,
           svc: VerificationService = Depends(get_verification_service)):
    """查询任务状态。"""
    status = svc.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse(data=status)
