"""房源信息验证应用服务：编排上传 -> 限流 -> 异步执行 -> 进度/产物。

- submit: 限流检查、保存上传件、创建任务、提交后台线程执行；
- get_status: 查询任务状态与产物清单（供轮询/SSE 外的快照查询）。
"""
from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.infrastructure.config.settings import Settings
from app.infrastructure.job_store import (JobStore, PENDING, get_store)
from app.infrastructure.rate_limiter import IPRateLimiter, RateDecision
from app.infrastructure.verification_runner import run_verification
from app.security.file_validation import UploadValidationError, validate_upload


class RateLimitExceeded(Exception):
    """触发限流。"""

    def __init__(self, decision: RateDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class VerificationService:
    """验证用例编排（单实例，线程安全的内存任务存储）。"""

    def __init__(self, settings: Settings, store: JobStore | None = None,
                 limiter: IPRateLimiter | None = None) -> None:
        self.settings = settings
        self.store = store or get_store()
        self.limiter = limiter or IPRateLimiter(
            settings.rate_per_minute, settings.rate_per_day)
        self._executor = ThreadPoolExecutor(
            max_workers=max(settings.max_concurrent_jobs, 1),
            thread_name_prefix="verify")
        self._semaphore = threading.Semaphore(
            max(settings.max_concurrent_jobs, 1))

    # ---- 提交 -----------------------------------------------------------
    def submit(self, ip: str, filename: str, data: bytes) -> dict:
        """校验并提交一次验证任务，返回任务创建视图。

        触发限流抛 RateLimitExceeded；上传不合规抛 UploadValidationError。
        """
        decision = self.limiter.check(ip)
        if not decision.allowed:
            raise RateLimitExceeded(decision)

        name = validate_upload(filename, data, self.settings.max_upload_mb)

        rec = self.store.create(ip, self._next_work_dir())
        uploads = Path(rec.work_dir) / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        cert_path = uploads / name
        cert_path.write_bytes(data)

        self.store.append_event(rec.job_id, "milestone", "上传完成，已加入队列")
        self._executor.submit(self._run, rec.job_id, cert_path)
        return {
            "job_id": rec.job_id,
            "status": rec.status,
            "remaining_minute": decision.remaining_minute,
            "remaining_day": decision.remaining_day,
        }

    def _next_work_dir(self) -> Path:
        return self.settings.jobs_root / f"{int(time.time() * 1000)}_{threading.get_ident()}"

    # ---- 后台执行 -------------------------------------------------------
    def _run(self, job_id: str, cert_path: Path) -> None:
        """后台线程入口：限并发后执行验证。"""
        with self._semaphore:
            self.store.mark_running(job_id)
            self.store.append_event(job_id, "milestone", "开始处理")
            run_verification(job_id, cert_path, self.settings, self.store)
            # 清理上传原件（含个人敏感信息，核验后即删）
            try:
                shutil.rmtree(Path(cert_path).parent, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    # ---- 查询 -----------------------------------------------------------
    def get_status(self, job_id: str) -> dict | None:
        rec = self.store.get(job_id)
        if rec is None:
            return None
        base = f"/api/v1/verification/{job_id}/download"
        return {
            "job_id": rec.job_id,
            "status": rec.status,
            "created_at": rec.created_at,
            "finished_at": rec.finished_at,
            "error": rec.error,
            "artifacts": [
                {
                    "spec": a["spec"], "title": a["title"],
                    "filename": a["filename"], "size": a["size"],
                    "content_type": a["content_type"],
                    "url": f"{base}/{a['spec']}",
                }
                for a in rec.artifacts
            ],
        }

    def shutdown(self) -> None:
        """优雅关闭线程池。"""
        self._executor.shutdown(wait=False, cancel_futures=True)
