"""房源信息验证应用服务：编排上传 -> 限流 -> 异步执行 -> 进度/产物。

- submit: 限流检查、保存上传件、创建任务、提交后台线程执行；
- get_status: 查询任务状态与产物清单（供轮询/SSE 外的快照查询）。
"""
from __future__ import annotations

import shutil
import threading
import time
from collections import deque
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


class QueueFull(Exception):
    """等待队列已满，拒绝继续接收上传文件。"""

    def __init__(self, max_queued_jobs: int) -> None:
        super().__init__("当前任务较多，请稍后再试")
        self.max_queued_jobs = max_queued_jobs


class VerificationService:
    """验证用例编排（单实例，线程安全的内存任务存储）。"""

    def __init__(self, settings: Settings, store: JobStore | None = None,
                 limiter: IPRateLimiter | None = None) -> None:
        self.settings = settings
        self.store = store or get_store()
        self.limiter = limiter or IPRateLimiter(
            settings.rate_per_minute, settings.rate_per_day)
        self._model_limiter = IPRateLimiter(
            settings.model_rate_per_minute, settings.model_rate_per_day)
        self._max_concurrent_jobs = max(settings.max_concurrent_jobs, 1)
        self._max_queued_jobs = max(settings.max_queued_jobs, 0)
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_concurrent_jobs,
            thread_name_prefix="verify")
        # 运行中 + 等待中的总容量，防止 ThreadPoolExecutor 的无界队列积压任务。
        self._admission = threading.BoundedSemaphore(
            self._max_concurrent_jobs + self._max_queued_jobs)
        self._queue_lock = threading.Lock()
        self._queued_job_ids: deque[str] = deque()
        self._counter_lock = threading.Lock()
        self._counter_path = self.settings.jobs_root / "served_count.txt"
        self._served_count = self._load_served_count()

    # ---- 提交 -----------------------------------------------------------
    def submit(self, ip: str, filename: str, data: bytes) -> dict:
        """校验并提交一次验证任务，返回任务创建视图。

        触发限流抛 RateLimitExceeded；上传不合规抛 UploadValidationError。
        """
        name = validate_upload(filename, data, self.settings.max_upload_mb)
        if not self._admission.acquire(blocking=False):
            raise QueueFull(self._max_queued_jobs)

        try:
            decision = self.limiter.check(ip)
            if not decision.allowed:
                raise RateLimitExceeded(decision)

            rec = self.store.create(ip, self._next_work_dir())
            uploads = Path(rec.work_dir) / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            cert_path = uploads / name
            cert_path.write_bytes(data)

            with self._queue_lock:
                queue_ahead = len(self._queued_job_ids)
                self._queued_job_ids.append(rec.job_id)
            self.store.append_event(
                rec.job_id, "queue",
                f"排队中，前方有 {queue_ahead} 个任务等待处理")
            self._executor.submit(self._run, rec.job_id, cert_path)
            served_count = self._increment_served_count()
            return {
                "job_id": rec.job_id,
                "status": rec.status,
                "served_count": served_count,
                "queue_ahead": queue_ahead,
                "queue_capacity": self._max_queued_jobs,
                "remaining_minute": decision.remaining_minute,
                "remaining_day": decision.remaining_day,
            }
        except Exception:
            self._admission.release()
            raise

    def _next_work_dir(self) -> Path:
        return self.settings.jobs_root / f"{int(time.time() * 1000)}_{threading.get_ident()}"

    def _load_served_count(self) -> int:
        try:
            return max(int(self._counter_path.read_text(encoding="utf-8").strip()), 0)
        except (OSError, ValueError):
            return 0

    def _increment_served_count(self) -> int:
        """持久化已受理任务数；服务重启后仍可在页面展示累计值。"""
        with self._counter_lock:
            self._served_count += 1
            temp_path = self._counter_path.with_suffix(".tmp")
            temp_path.write_text(str(self._served_count), encoding="utf-8")
            temp_path.replace(self._counter_path)
            return self._served_count

    # ---- 后台执行 -------------------------------------------------------
    def _run(self, job_id: str, cert_path: Path) -> None:
        """后台线程入口：限并发后执行验证。"""
        try:
            with self._queue_lock:
                try:
                    self._queued_job_ids.remove(job_id)
                except ValueError:
                    pass
                queued = list(self._queued_job_ids)
            self._publish_queue_positions(queued)
            self.store.mark_running(job_id)
            self.store.append_event(job_id, "milestone", "已轮到您，开始处理")
            run_verification(
                job_id, cert_path, self.settings, self.store,
                before_model_request=lambda: self._wait_for_model_slot(job_id),
            )
            # 清理上传原件（含个人敏感信息，核验后即删）
            try:
                shutil.rmtree(Path(cert_path).parent, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        finally:
            self._admission.release()

    def _publish_queue_positions(self, queued: list[str]) -> None:
        """每次有任务出队时，向仍在等待的用户推送最新位置。"""
        for position, queued_job_id in enumerate(queued):
            self.store.append_event(
                queued_job_id, "queue",
                f"排队中，前方有 {position} 个任务等待处理")

    def _wait_for_model_slot(self, job_id: str) -> None:
        """在调用模型前守住账户级预算；分钟级满载时等待，日额度耗尽则失败。"""
        announced = False
        while True:
            decision = self._model_limiter.check("openrouter")
            if decision.allowed:
                return
            if "每日" in decision.reason:
                raise RuntimeError("今日识别服务额度已用完，请明日再试")
            if not announced:
                self.store.append_event(
                    job_id, "queue",
                    "识别服务繁忙，正在等待可用调用额度")
                announced = True
            time.sleep(min(max(decision.retry_after_seconds, 1), 60))

    # ---- 查询 -----------------------------------------------------------
    def get_stats(self) -> dict:
        return {"served_count": self._served_count}

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
