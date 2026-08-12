"""Export the local CRM featured feed and copy one snapshot to the cloud.

This remains a manual recovery utility. Normal periodic updates are owned by
the local crm_connector process: its in-process daemon thread
(app.application.featured_snapshot_push) runs on a fixed interval controlled
by CC_FEATURED_PUSH_* settings injected at launch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.infrastructure.featured_fetcher import FeaturedListingsFetcher  # noqa: E402


def main() -> int:
    crm_url = os.getenv("FEATURED_CRM_CONNECTOR_URL", "http://127.0.0.1:8020")
    remote_host = os.getenv("FEATURED_REMOTE_HOST", "beike-server")
    remote_path = os.getenv(
        "FEATURED_REMOTE_PATH", "/var/lib/store-media/featured_snapshot.json"
    )
    if not remote_path.startswith("/var/lib/store-media/"):
        raise SystemExit("FEATURED_REMOTE_PATH must stay under /var/lib/store-media")

    feed = FeaturedListingsFetcher(crm_url, cache_seconds=0).latest()
    if not feed.sale and not feed.rent:
        raise SystemExit("CRM returned an empty featured feed; remote snapshot was not changed")

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", delete=False
    ) as handle:
        json.dump(asdict(feed), handle, ensure_ascii=False, separators=(",", ":"))
        local_path = Path(handle.name)

    remote_tmp = f"{remote_path}.tmp"
    try:
        subprocess.run(
            ["scp", str(local_path), f"{remote_host}:{remote_tmp}"],
            check=True,
        )
        subprocess.run(
            ["ssh", remote_host, "mv", remote_tmp, remote_path],
            check=True,
        )
    finally:
        local_path.unlink(missing_ok=True)

    print(
        f"featured snapshot uploaded: sale={len(feed.sale)} rent={len(feed.rent)} "
        f"updated_at={feed.updated_at}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
