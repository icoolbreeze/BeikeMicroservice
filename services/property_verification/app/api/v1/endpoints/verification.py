"""房源信息验证端点。

接口：
- POST /verification            上传不动产权证图片，创建验证任务（无需登录）。
- GET  /verification/{id}/events  SSE 实时进度与错误流。
- GET  /verification/{id}/result  结构化核验结果（官方查询表格解析）。
- GET  /verification/{id}/artifacts  产物清单。
- GET  /verification/{id}/download/{spec}  下载指定规格截图或打包。

安全：无需登录；按 IP 限流（默认 2 次/分钟、30 次/天）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import (get_client_ip, get_job_store,
                                  get_verification_service, get_settings)
from app.application.services.verification_service import (
    QueueFull, RateLimitExceeded, VerificationService)
from app.infrastructure.job_store import JobStore
from app.schemas.common import ApiResponse
from app.security.file_validation import UploadValidationError
from app.security.share_token import (process_secret, sign_share_token,
                                      verify_share_token)

router = APIRouter(prefix="/verification", tags=["verification"])


@router.post("", status_code=202, response_model=ApiResponse)
async def create_verification(file: UploadFile = File(...),
                             svc: VerificationService = Depends(
                                 get_verification_service),
                             request: Request = None):
    """上传产权证图片，发起验证任务。"""
    data = await file.read()
    ip = get_client_ip(request)
    try:
        result = svc.submit(ip, file.filename or "upload.jpg", data)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": 429, "message": exc.decision.reason,
                "retry_after": exc.decision.retry_after_seconds,
                "remaining_minute": exc.decision.remaining_minute,
                "remaining_day": exc.decision.remaining_day,
            })
    except QueueFull as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": 503,
                "message": "当前排队任务较多，请稍后再试",
                "queue_capacity": exc.max_queued_jobs,
            })
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiResponse(data=result)


@router.get("/stats", response_model=ApiResponse)
def verification_stats(
    svc: VerificationService = Depends(get_verification_service),
):
    """服务累计受理次数，供页面顶部展示。"""
    return ApiResponse(data=svc.get_stats())


@router.get("/{job_id}/events")
async def job_events(job_id: str):
    """SSE 推送任务进度，直到终态。"""
    store: JobStore = get_job_store()
    if store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def stream():
        index = 0
        last_ping = 0
        while True:
            rec = store.get(job_id)
            if rec is None:
                yield _sse({"type": "error", "message": "任务不存在"})
                return
            new = store.events_since(job_id, index)
            index += len(new)
            for ev in new:
                yield _sse({"type": ev.type, "message": ev.message,
                            "ts": ev.ts})
            if rec.is_terminal():
                # 推送终态快照后关闭
                yield _sse({"type": "terminal", "status": rec.status,
                            "error": rec.error})
                return
            # 每 ~15s 发一次心跳，保活连接
            now = asyncio.get_event_loop().time()
            if now - last_ping > 15:
                yield ": ping\n\n"
                last_ping = now
            await asyncio.sleep(0.4)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # nginx 不缓冲
    }
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers=headers)


@router.get("/{job_id}/result", response_model=ApiResponse)
def job_result(job_id: str,
               svc: VerificationService = Depends(get_verification_service)):
    """结构化核验结果（服务端在官方查询时解析的页面表格数据）。"""
    status = svc.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse(data={"status": status["status"],
                             "result": status["result"]})


@router.get("/{job_id}/artifacts", response_model=ApiResponse)
def job_artifacts(job_id: str,
                 svc: VerificationService = Depends(get_verification_service)):
    """产物清单。"""
    status = svc.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse(data={"status": status["status"],
                             "artifacts": status["artifacts"]})


@router.get("/{job_id}/share/{spec}", response_model=ApiResponse)
def share_link(job_id: str, spec: str, ttl: int = 600,
               svc: VerificationService = Depends(get_verification_service)):
    """生成短期签名下载链接（供转发给最终用户；原始 job_id 访问不受影响）。"""
    status = svc.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not any(a["spec"] == spec for a in status["artifacts"]):
        raise HTTPException(status_code=404, detail="无此规格产物")
    settings = get_settings()
    max_ttl = max(settings.download_token_ttl_seconds, 60)
    ttl = min(max(ttl, 10), max_ttl)
    expires_at = int(time.time()) + ttl
    token = sign_share_token(job_id, spec, expires_at,
                             process_secret(settings.download_token_secret))
    return ApiResponse(data={
        "url": f"/api/v1/verification/{job_id}/download/{spec}?token={token}",
        "expires_at": expires_at,
        "ttl_seconds": ttl,
    })


@router.get("/{job_id}/download/{spec}")
def download_artifact(job_id: str, spec: str, token: str | None = None,
                      svc: VerificationService = Depends(
                          get_verification_service)):
    """下载指定规格产物（panel / full / zip）。

    带 ``token`` 时按短期签名校验（转发场景），无 token 时维持
    job_id 直接访问（本地工具/MCP 拉取）。
    """
    if token is not None:
        settings = get_settings()
        if not verify_share_token(job_id, spec, token,
                                  process_secret(settings.download_token_secret)):
            raise HTTPException(status_code=403, detail="链接无效或已过期")
    status = svc.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    for a in status["artifacts"]:
        if a["spec"] == spec:
            p = Path(_artifact_path(job_id, a["filename"]))
            if not p.exists():
                raise HTTPException(status_code=410, detail="文件已失效")
            return FileResponse(p, media_type=a["content_type"],
                                filename=a["filename"])
    raise HTTPException(status_code=404, detail="无此规格产物")


def _artifact_path(job_id: str, filename: str) -> str:
    """根据任务工作目录拼接产物绝对路径。"""
    rec = get_job_store().require(job_id)
    return str(Path(rec.work_dir) / filename)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
