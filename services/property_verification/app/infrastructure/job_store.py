"""验证任务的内存存储与进度事件。

每个任务记录状态、事件流（进度/日志/错误）与产物文件。线程安全。
任务 ID 使用 uuid4，对未登录用户即作为访问凭据（不可猜测）。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# 任务状态
PENDING = "pending"        # 已入队，等待执行
RUNNING = "running"         # 执行中
SUCCEEDED = "succeeded"     # 成功
FAILED = "failed"           # 失败

_TERMINAL = {SUCCEEDED, FAILED}


@dataclass
class ProgressEvent:
    """一条进度事件。"""

    ts: float
    type: str          # milestone | log | error | done
    message: str


@dataclass
class JobRecord:
    """单次验证任务的全部状态。"""

    job_id: str
    ip: str
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    events: list[ProgressEvent] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)  # [{spec, filename, path, size, content_type}]
    error: str | None = None
    work_dir: str = ""

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


class JobStore:
    """进程内任务注册表。"""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def create(self, ip: str, work_dir: Path) -> JobRecord:
        job_id = uuid.uuid4().hex
        rec = JobRecord(job_id=job_id, ip=ip, work_dir=str(work_dir))
        with self._lock:
            self._jobs[job_id] = rec
        return rec

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def require(self, job_id: str) -> JobRecord:
        rec = self.get(job_id)
        if rec is None:
            raise KeyError(job_id)
        return rec

    def append_event(self, job_id: str, etype: str, message: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            rec.events.append(ProgressEvent(time.time(), etype, message))

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None and rec.status == PENDING:
                rec.status = RUNNING

    def set_artifacts(self, job_id: str, artifacts: Iterable[dict]) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                rec.artifacts = list(artifacts)

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                rec.status = status
                rec.finished_at = time.time()
                rec.error = error

    def events_since(self, job_id: str, index: int) -> list[ProgressEvent]:
        """返回下标 >= index 的事件（供 SSE 增量推送）。"""
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return []
            return list(rec.events[index:])

    def purge(self, older_than_ts: float) -> int:
        """驱逐创建时间早于 ``older_than_ts`` 的任务记录，返回移除数量。"""
        with self._lock:
            stale = [job_id for job_id, rec in self._jobs.items()
                     if rec.created_at < older_than_ts]
            for job_id in stale:
                del self._jobs[job_id]
            return len(stale)


# 进程级单例（单实例部署）
_store: JobStore | None = None


def get_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore()
    return _store
