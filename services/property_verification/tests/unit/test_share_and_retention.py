"""短期签名链接与产物保留期清理的单元测试。"""
from __future__ import annotations

import os
import time

from app.infrastructure.config.settings import Settings
from app.infrastructure.job_store import JobStore
from app.application.services.verification_service import VerificationService
from app.security.share_token import (sign_share_token, verify_share_token)


class _FakeExecutor:
    def submit(self, *_args, **_kwargs) -> None:
        return None

    def shutdown(self, **_kwargs) -> None:
        return None


def test_sign_and_verify_roundtrip():
    secret = "test-secret"
    expires = int(time.time()) + 600
    token = sign_share_token("job1", "panel", expires, secret)
    assert verify_share_token("job1", "panel", token, secret) is True


def test_verify_rejects_wrong_job_or_spec():
    secret = "test-secret"
    expires = int(time.time()) + 600
    token = sign_share_token("job1", "panel", expires, secret)
    assert verify_share_token("job2", "panel", token, secret) is False
    assert verify_share_token("job1", "full", token, secret) is False


def test_verify_rejects_tampered_and_expired():
    secret = "test-secret"
    expires = int(time.time()) + 600
    token = sign_share_token("job1", "panel", expires, secret)
    assert verify_share_token("job1", "panel", token + "x", secret) is False
    assert verify_share_token(
        "job1", "panel", token, secret, now=expires + 1) is False
    assert verify_share_token(
        "job1", "panel", token, secret, now=expires - 1) is True


def test_verify_rejects_malformed():
    assert verify_share_token("job1", "panel", "garbage", "s") is False
    assert verify_share_token("job1", "panel", "", "s") is False


def test_job_store_purge(tmp_path):
    store = JobStore()
    old = store.create("10.0.0.1", tmp_path / "a")
    old.created_at = 1000.0
    fresh = store.create("10.0.0.2", tmp_path / "b")
    fresh.created_at = 999999999.0

    removed = store.purge(5000.0)
    assert removed == 1
    assert store.get(old.job_id) is None
    assert store.get(fresh.job_id) is not None


def test_purge_expired_removes_old_job_dirs(tmp_path):
    jobs_root = tmp_path / "verify_jobs"
    old_dir = jobs_root / "old_job"
    fresh_dir = jobs_root / "fresh_job"
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    old_ts = time.time() - 10 * 86400
    fresh_ts = time.time()
    os.utime(old_dir, (old_ts, old_ts))
    os.utime(fresh_dir, (fresh_ts, fresh_ts))

    settings = Settings(storage_dir=str(tmp_path), artifact_retention_days=7)
    service = VerificationService(settings, JobStore())
    service._executor = _FakeExecutor()
    try:
        service._purge_expired()
        assert not old_dir.exists()
        assert fresh_dir.exists()
    finally:
        service.shutdown()


def test_purge_expired_evicts_memory_records(tmp_path):
    jobs_root = tmp_path / "verify_jobs"
    jobs_root.mkdir(parents=True)
    store = JobStore()
    rec = store.create("10.0.0.1", jobs_root / "old_job")
    rec.created_at = time.time() - 10 * 86400

    settings = Settings(storage_dir=str(tmp_path), artifact_retention_days=7)
    service = VerificationService(settings, store)
    service._executor = _FakeExecutor()
    try:
        service._purge_expired()
        assert store.get(rec.job_id) is None
    finally:
        service.shutdown()
