"""Small, bounded worker telemetry. Failures here never stop business processing."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from app.infrastructure.archive_vision_store import ArchiveVisionStore

logger = logging.getLogger(__name__)


def new_model_stats() -> dict[str, Any]:
    return {
        "model_calls": 0,
        "model_seconds": 0.0,
        "http_attempts": 0,
        "usage_responses": 0,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def record_usage(stats: dict | None, usage: Any) -> None:
    if stats is None or not isinstance(usage, dict):
        return
    found = False
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            stats[key] = (stats.get(key) or 0) + value
            found = True
    if found:
        stats["usage_responses"] += 1


class WorkerTelemetry:
    def __init__(self, path: str, min_free_gib: float, interval: float = 15):
        self.store = ArchiveVisionStore(path)
        self.interval = interval
        self.data: dict[str, Any] = {
            "instance_id": str(uuid.uuid4()),
            "state": "idle",
            "last_success_at": None,
            "min_free_gib": min_free_gib,
            "disk_free_gib": None,
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _publish(self) -> None:
        import json

        try:
            with self._lock:
                self.data["updated_at"] = datetime.now(UTC).isoformat()
                from pathlib import Path

                if Path(self.store.db_path).is_file():
                    self.store.set_meta("dashboard:heartbeat", json.dumps(self.data))
                else:
                    # Storage guard: heartbeat must not initialize a missing vision DB.
                    sidecar = Path(self.store.db_path + ".status.json")
                    temporary = sidecar.with_suffix(".tmp")
                    temporary.write_text(json.dumps(self.data), encoding="utf-8")
                    temporary.replace(sidecar)
        except Exception:
            logger.warning("archive dashboard heartbeat unavailable")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._publish()

    def __enter__(self):
        self._publish()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vision-heartbeat")
        self._thread.start()
        return self

    def state(self, state: str, **values: Any) -> None:
        with self._lock:
            self.data.update(state=state, **values)
        self._publish()

    def batch(self, metrics: dict) -> None:
        import json

        metrics = {**metrics, "computed_at": datetime.now(UTC).isoformat()}
        try:
            from pathlib import Path

            if Path(self.store.db_path).is_file():
                self.store.set_meta("dashboard:batch", json.dumps(metrics))
        except Exception:
            logger.warning("archive dashboard batch metrics unavailable")
        values = {}
        if metrics.get("success", 0) or metrics.get("cache_hit", 0):
            values["last_success_at"] = metrics["computed_at"]
        self.state(
            "paused_storage" if metrics.get("status") == "paused_storage" else "idle", **values
        )

    def close(self, error: bool = False) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self.state("error" if error else "stopped")

    def __exit__(self, exc_type, exc, tb):
        self.close(error=exc_type is not None)
