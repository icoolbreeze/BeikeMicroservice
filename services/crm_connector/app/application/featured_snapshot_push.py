"""Periodic featured-feed export owned by the local CRM Connector process.

The cloud server never opens a connection to this Windows machine.  The
Connector collects the same featured feed used by the display and atomically
uploads one JSON snapshot over the existing SSH alias.  A failed collection or
upload leaves the cloud's last successful snapshot intact.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
import sys

from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)


def _load_featured_fetcher() -> type:
    """Load the shared display collection strategy without importing another app.

    ``store_media`` and ``crm_connector`` both use the Python package name
    ``app``. Loading the single strategy module by file path avoids that name
    collision while retaining exactly the existing featured selection rules.
    """
    services_dir = Path(__file__).resolve().parents[3]
    source = services_dir / "store_media" / "app" / "infrastructure" / "featured_fetcher.py"
    spec = importlib.util.spec_from_file_location("store_media_featured_fetcher", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"featured collection strategy is unavailable: {source}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve postponed annotations through sys.modules while the
    # imported module is being executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "FeaturedListingsFetcher")


class FeaturedSnapshotPusher:
    """One daemon thread; its cadence is controlled by connector settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="crm-featured-snapshot-pusher",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        # Uvicorn has to finish binding before the first self-HTTP collection.
        if self._stop.wait(3):
            return
        while not self._stop.is_set():
            self.push_once()
            self._stop.wait(self._settings.featured_push_interval_seconds)

    def push_once(self) -> bool:
        try:
            if not self._settings.featured_push_remote_path.startswith("/var/lib/store-media/"):
                raise ValueError("featured snapshot path must stay under /var/lib/store-media")
            fetcher_type = _load_featured_fetcher()
            feed = fetcher_type(
                self._settings.featured_push_local_api_base_url,
                cache_seconds=0,
            ).latest()
            if not feed.sale and not feed.rent:
                logger.warning("featured_snapshot_push.skipped reason=empty_feed")
                return False
            self._upload(asdict(feed))
            logger.info(
                "featured_snapshot_push.succeeded sale=%s rent=%s updated_at=%s",
                len(feed.sale), len(feed.rent), feed.updated_at,
            )
            return True
        except Exception as exc:  # boundary: background work must not kill CRM service
            logger.warning("featured_snapshot_push.failed class=%s", exc.__class__.__name__)
            return False

    def _upload(self, payload: dict[str, object]) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            local_path = Path(handle.name)
        remote_path = self._settings.featured_push_remote_path
        try:
            subprocess.run(
                ["scp", str(local_path), f"{self._settings.featured_push_remote_host}:{remote_path}.tmp"],
                check=True,
                capture_output=True,
                timeout=120,
            )
            subprocess.run(
                ["ssh", self._settings.featured_push_remote_host, "mv", f"{remote_path}.tmp", remote_path],
                check=True,
                capture_output=True,
                timeout=30,
            )
        finally:
            local_path.unlink(missing_ok=True)
