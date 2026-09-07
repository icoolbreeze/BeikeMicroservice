"""队列 B 端到端:假 connector → 真 RoughcastCrawlClient → 参考快照终态。"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.application.roughcast_crawler import RoughcastCrawlConfig
from app.application.roughcast_queue_b import RoughcastQueueBCrawler
from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import RoughcastCrawlClient
from app.infrastructure.roughcast_repository import COMPLETE, RoughcastRepository
from tests.roughcast_helpers import FakeClock, FakeMonotonic, RecordingSleeper, seed_community

PAGE_SIZE = 2


def raw_listing(listing_id: str, *, rent: float = 4300.0, fitment: str | None = "003",
                resblock: str = "RB001", community: str = "甲小区") -> dict:
    return {
        "listing_id": listing_id,
        "resblock_name": community,
        "resblock_id": resblock,
        "biz_circle": "华侨城",
        "layout": "2室1厅1卫",
        "bedroom_amount": 2,
        "hall_amount": 1,
        "bathroom_amount": 1,
        "area_sqm": 88.5,
        "monthly_rent_yuan": rent,
        "orientation": "南",
        "floor_desc": "中楼层",
        "total_floors": 18,
        "rent_mode_label": "整租",
        "del_type": 1,
        "fitment_status": fitment,
        "fitment_status_desc": "精装" if fitment == "003" else None,
        "create_time": "2026-06-01T00:00:00+00:00",
        "title_image_url": "https://img.example.com/a.jpg",
        "owner_phone": "13800000000",
        "upload_user": "内部账号",
    }


class FakeConnector:
    def __init__(self):
        self.listings: dict[str, list[dict]] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://127.0.0.1:{port}"

    def start(self) -> None:
        connector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: A003
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                connector.requests.append(("POST", self.path, body))
                ids = body.get("resblock_ids") or []
                items = connector.listings.get(ids[0], []) if ids else []
                page = int(body.get("page", 1))
                start = (page - 1) * PAGE_SIZE
                chunk = items[start:start + PAGE_SIZE]
                payload = json.dumps({
                    "items": chunk,
                    "total": len(items),
                    "page": page,
                    "page_size": PAGE_SIZE,
                    "has_more": start + PAGE_SIZE < len(items),
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def connector():
    fake = FakeConnector()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


def test_queue_b_end_to_end_writes_reference_snapshot(tmp_path, connector) -> None:
    connector.listings = {
        "RB001": [
            raw_listing("R1", fitment="003"),
            raw_listing("R2", fitment="001"),
            raw_listing("L1", fitment="002"),
            raw_listing("U1", fitment=None),
        ],
    }
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    seed_community(database, community_id="RB001", name="甲小区")
    repository = RoughcastRepository(database, clock=clock)
    monotonic = FakeMonotonic()
    crawler = RoughcastQueueBCrawler(
        repository,
        RoughcastCrawlClient(connector.base_url, page_size=PAGE_SIZE),
        RoughcastCrawlConfig(
            page_size=PAGE_SIZE,
            page_gap_seconds=(0.0, 0.0),
            long_pause_seconds=(0.0, 0.0),
            min_request_interval_seconds=0.0,
        ),
        clock=clock,
        monotonic=monotonic,
        sleeper=RecordingSleeper(monotonic),
        rng=random.Random(7),
    )

    outcome = crawler.run_once()

    assert outcome is not None
    assert outcome.status == COMPLETE
    assert outcome.pages_done == 2
    assert outcome.reference_rows == 4
    assert outcome.reference_r_rows == 2

    assert connector.requests, "没发出任何请求"
    for method, path, body in connector.requests:
        assert method == "POST"
        assert path == "/api/v1/listings/rental/search"
        assert body["scope"] == "all"
        assert body["resblock_ids"] == ["RB001"]
        assert "fitment" not in body.get("condition_filters", {})
        assert "condition_filters" not in body or body["condition_filters"] == {}

    rows = {row["listing_id"]: row for row in repository.reference_snapshot_for("RB001")}
    assert set(rows) == {"R1", "R2", "L1", "U1"}
    assert rows["L1"]["is_roughcast"] == 1
    assert rows["R1"]["is_roughcast"] == 0
    assert rows["U1"]["fitment_status"] is None
    assert repository.community("RB001")["reference_run_id"] == outcome.run_id
    assert repository.active_count() == 0
