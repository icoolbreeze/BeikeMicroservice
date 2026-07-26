from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.application.services.verification_service import QueueFull, VerificationService
from app.infrastructure.config.settings import Settings
from app.infrastructure.job_store import JobStore


class _FakeExecutor:
    def submit(self, *_args, **_kwargs) -> None:
        return None

    def shutdown(self, **_kwargs) -> None:
        return None


def _jpeg() -> bytes:
    image = Image.new("RGB", (4, 4), "white")
    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_queue_rejects_after_running_and_waiting_capacity(tmp_path) -> None:
    settings = Settings(
        storage_dir=str(tmp_path),
        max_concurrent_jobs=3,
        max_queued_jobs=12,
        rate_per_minute=100,
        rate_per_day=100,
    )
    service = VerificationService(settings, JobStore())
    service._executor = _FakeExecutor()

    for index in range(15):
        result = service.submit(f"10.0.0.{index}", "cert.jpg", _jpeg())
        assert result["queue_capacity"] == 12

    try:
        service.submit("10.0.1.1", "cert.jpg", _jpeg())
    except QueueFull as exc:
        assert exc.max_queued_jobs == 12
    else:
        raise AssertionError("the 16th accepted task should be rejected")
