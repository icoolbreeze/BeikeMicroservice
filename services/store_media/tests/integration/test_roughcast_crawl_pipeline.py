"""端到端:假 connector → 真 RoughcastCrawlClient → 库表终态（第 1 期集成）。

用一个本地 HTTP 服务器当假 connector,所以 `RoughcastCrawlClient` 的请求组装、
响应解包、错误码解析这些 glue code 都被真正跑到——mock 掉客户端就测不到它们。
**不发任何真实上游请求。**
"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.application.roughcast_crawler import RoughcastCrawlConfig, RoughcastCrawler
from app.infrastructure.database import Database
from app.infrastructure.roughcast_crawl_client import RoughcastCrawlClient
from app.infrastructure.roughcast_repository import ABORTED, COMPLETE, RoughcastRepository
from app.infrastructure.roughcast_rental_fetcher import RoughcastRentalFeed
from app.infrastructure.settings import Settings
from app.main import create_app
from tests.roughcast_helpers import FakeClock, FakeMonotonic, RecordingSleeper

PAGE_SIZE = 2


def raw_listing(listing_id: str, *, rent: float = 4300.0, fitment: str | None = "002",
                resblock: str = "RB001", community: str = "甲小区",
                bizcircle: str | None = "华侨城") -> dict:
    """connector `/api/v1/listings/rental/search` 的行形状。"""
    return {
        "listing_id": listing_id,
        "resblock_name": community,
        "resblock_id": resblock,
        "biz_circle": bizcircle,
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
        "fitment_status_desc": "毛坯",
        "create_time": "2026-06-01T00:00:00+00:00",
        "title_image_url": "https://img.example.com/a.jpg",
        # 隐私字段:必须一路被丢弃,不进库不出网(§五)。
        "owner_phone": "13800000000",
        "upload_user": "内部账号",
    }


class FakeConnector:
    """按轮次返回预置分页。记录每一次请求的方法、路径与 body。"""

    def __init__(self):
        self.rounds: list[list[dict]] = []
        self.round_index = 0
        self.requests: list[tuple[str, str, dict]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.status_override: int | None = None
        self.error_code: str | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://127.0.0.1:{port}"

    def start(self) -> None:
        connector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):    # noqa: A003 —— 别把 HTTP 日志喷到测试输出
                pass

            def do_GET(self):               # noqa: N802
                connector.requests.append(("GET", self.path, {}))
                self.send_error(404)

            def do_POST(self):              # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                connector.requests.append(("POST", self.path, body))
                if connector.status_override is not None:
                    payload = json.dumps(
                        {"detail": {"code": connector.error_code, "message": "boom"}}
                    ).encode("utf-8")
                    self.send_response(connector.status_override)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self._send_page(body)

            def _send_page(self, body: dict):
                items = connector.rounds[connector.round_index]
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


def build_crawler(database: Database, connector: FakeConnector, clock: FakeClock):
    monotonic = FakeMonotonic()
    return RoughcastCrawler(
        RoughcastRepository(database, clock=clock),
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


def test_first_round_end_to_end(tmp_path, connector) -> None:
    connector.rounds = [
        # 5 套毛坯 + 1 套精装 + 1 套装修未知,共 4 页。
        [
            raw_listing("L1"), raw_listing("L2"), raw_listing("L3"),
            raw_listing("L4", resblock="RB002", community="乙小区"),
            raw_listing("L5", resblock="RB002", community="乙小区"),
            raw_listing("R1", fitment="003"),
            raw_listing("U1", fitment=None),
        ],
    ]
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    repository = RoughcastRepository(database, clock=clock)

    outcome = build_crawler(database, connector, clock).run_once()

    assert outcome.status == COMPLETE
    assert outcome.pages_expected == 4               # ceil(7 / 2),不是写死的
    assert outcome.published == 5                    # 只有 fitment=002 进被排序集 P
    assert outcome.snapshots_inserted == 5
    assert outcome.deactivated == 0

    run = repository.get_run(outcome.run_id)
    assert (run["items_seen"], run["non_roughcast_count"], run["unknown_fitment_count"]) == (7, 1, 1)
    assert run["request_count"] == 4
    assert run["upstream_total"] == 7

    # 逐表终态。
    assert repository.active_count() == 5
    assert repository.stage_count(outcome.run_id) == 7        # 非毛坯与未知也落库
    assert repository.community("RB001")["roughcast_count"] == 3
    assert repository.community("RB002")["roughcast_count"] == 2
    row = repository.get_current("L1")
    assert row["monthly_rent_yuan"] == 4300.0
    assert row["rooms"] == 2 and row["halls"] == 1 and row["baths"] == 1
    assert row["rent_mode"] == "整租"
    assert row["fitment_status"] == "002"
    assert row["bizcircle"] == "华侨城"             # 改动 E 已落地,不再恒 None
    assert row["create_time"] == "2026-06-01T00:00:00+00:00"
    with database.connect() as db:
        logged = db.execute(
            "SELECT target, status FROM roughcast_crawl_log ORDER BY id"
        ).fetchall()
    # V2.5:11 档切分,默认单档 `((None, None),)` 渲染为 `0:-1`,所以
    # crawl_log 的 target 也带 bucket 前缀。
    assert [r["target"] for r in logged] == [
        f"bucket=0:-1&page={n}" for n in (1, 2, 3, 4)
    ]
    assert {r["status"] for r in logged} == {"ok"}


def test_second_round_applies_changes(tmp_path, connector) -> None:
    connector.rounds = [
        [raw_listing("L1"), raw_listing("L2"), raw_listing("L3")],
        [raw_listing("L1", rent=4600.0), raw_listing("L2")],
    ]
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    repository = RoughcastRepository(database, clock=clock)

    build_crawler(database, connector, clock).run_once()
    first_seen = repository.get_current("L1")["first_seen_at"]

    clock.advance(days=1)
    connector.round_index = 1
    second = build_crawler(database, connector, clock).run_once()

    assert second.status == COMPLETE
    assert second.published == 2
    assert second.snapshots_inserted == 1            # 只有 L1 变了
    assert second.deactivated == 1                   # L3 消失

    current = repository.get_current("L1")
    assert current["monthly_rent_yuan"] == 4600.0
    assert current["first_seen_at"] == first_seen     # 只能保留旧值
    assert [row["monthly_rent_yuan"] for row in repository.snapshots_for("L1")] == [4300.0, 4600.0]
    assert repository.get_current("L3")["is_active"] == 0
    # 没变的 L2 只前移新鲜度,不新增快照行。
    l2_snapshots = repository.snapshots_for("L2")
    assert len(l2_snapshots) == 1
    assert l2_snapshots[0]["last_confirmed_run_id"] == second.run_id


def test_no_detail_or_get_request_is_ever_issued(tmp_path, connector) -> None:
    """§三:抓取阶段只打搜索接口。"""
    connector.rounds = [[raw_listing("L1"), raw_listing("L2"), raw_listing("L3")]]
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()

    build_crawler(database, connector, clock).run_once()

    assert connector.requests, "没发出任何请求,测试本身失效了"
    for method, path, body in connector.requests:
        assert method == "POST"
        assert path == "/api/v1/listings/rental/search"
        assert body["scope"] == "all"
        # V2.5:11 档切分,即使单档也要带 `price=0:-1`(V2.4 走的是无价过滤)。
        # 上游 connector 的 `lo:hi` 协议(5d2e06e 起 `priceMin`/`priceMax` 已删)。
        assert body["condition_filters"] == {"fitment": "002", "price": "0:-1"}
        assert body["page_size"] == PAGE_SIZE


def test_private_upstream_fields_never_reach_the_database(tmp_path, connector) -> None:
    """§五:owner_phone / upload_user 永不出库。入库模型里根本没有这两列。"""
    connector.rounds = [[raw_listing("L1"), raw_listing("L2")]]
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()

    build_crawler(database, connector, clock).run_once()

    with database.connect() as db:
        tables = [row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'roughcast_%'"
        )]
        dumped = ""
        for table in tables:
            columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
            assert "owner_phone" not in columns
            assert "upload_user" not in columns
            for row in db.execute(f"SELECT * FROM {table}"):
                dumped += "".join(str(value) for value in tuple(row))

    # stage 的 payload_json 是自由文本,必须确认它也没夹带隐私字段。
    assert "13800000000" not in dumped
    assert "owner_phone" not in dumped
    assert "upload_user" not in dumped


def test_bizcircle_is_stored_but_stays_out_of_the_change_hash(tmp_path, connector) -> None:
    """改动 E:商圈入库,但不参与变更判定。

    两件事一起钉住:
    1. `''` 与字段缺失都收敛成 NULL。商圈名没有「空串与未知语义不同」的问题
       ——这一点与 fitment_status 相反(见 domain/roughcast.py 的字段注释)。
    2. bizcircle 不在 HASH_FIELDS 里。否则 E 落地后的第一轮会把每一行都判成
       变更点、快照表凭空翻一倍,而唯一症状只是「快照表长得有点快」。
    """
    connector.rounds = [
        [raw_listing("L1"), raw_listing("L2", bizcircle="")],
        [raw_listing("L1", bizcircle="大面"), raw_listing("L2", bizcircle=None)],
    ]
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    repository = RoughcastRepository(database, clock=clock)

    first = build_crawler(database, connector, clock).run_once()
    assert first.snapshots_inserted == 2
    assert repository.get_current("L1")["bizcircle"] == "华侨城"
    assert repository.get_current("L2")["bizcircle"] is None      # '' 收敛成 NULL

    clock.advance(days=1)
    connector.round_index = 1
    second = build_crawler(database, connector, clock).run_once()

    assert second.status == COMPLETE
    # 商圈变了、业务列跟着更新,但它不进哈希,所以一行快照都不该新增。
    assert second.snapshots_inserted == 0
    assert repository.get_current("L1")["bizcircle"] == "大面"
    assert len(repository.snapshots_for("L1")) == 1


def test_auth_required_from_connector_aborts_the_run(tmp_path, connector) -> None:
    """connector 报 401 + CRM_AUTH_REQUIRED 是即时熔断信号,当轮 ABORTED 且不发布。"""
    connector.rounds = [[raw_listing("L1"), raw_listing("L2")]]
    connector.status_override = 401
    connector.error_code = "CRM_AUTH_REQUIRED"
    clock = FakeClock()
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()

    outcome = build_crawler(database, connector, clock).run_once()

    assert outcome.status == ABORTED
    assert outcome.reason == "breaker_open"
    assert len(connector.requests) == 1              # 熔断后不重试打穿
    assert RoughcastRepository(database).active_count() == 0


# ------------------------------------------------------------------ 服务装配


def test_crawler_thread_is_off_by_default(tmp_path) -> None:
    """第 1 期默认不起常驻线程:零上游流量,靠 scripts/roughcast_crawl_once.py 手动跑。"""
    app = create_app(Settings(storage_dir=tmp_path))

    with TestClient(app):
        assert app.state.roughcast_crawler is None
        assert app.state.roughcast_loop is None

    names = [thread.name for thread in threading.enumerate()]
    assert "roughcast-crawler" not in names
    assert "roughcast-daily-loop" not in names


def test_crawler_thread_starts_and_stops_when_enabled(tmp_path) -> None:
    """开 `SM_ROUGHCAST_CRAWL_ENABLED=1` → `roughcast-daily-loop` 线程起来。

    旧 A 守护线程名 `roughcast-crawler` 已经退役:它被日 loop 接管,
    否则同日 A 会跑两轮把硬顶撞穿。所以这里只看 `roughcast-daily-loop`,
    也不去查 `roughcast-crawler`。
    """
    app = create_app(Settings(storage_dir=tmp_path, roughcast_crawl_enabled=True))

    with TestClient(app):
        assert app.state.roughcast_loop is not None
        # 兼容旧探针:loop 把 A 实例挂在 `roughcast_crawler` 上,但不启它的 daemon
        assert app.state.roughcast_crawler is not None
        names = [t.name for t in threading.enumerate()]
        assert "roughcast-daily-loop" in names
        assert "roughcast-crawler" not in names

    assert "roughcast-daily-loop" not in [t.name for t in threading.enumerate()]


def test_display_api_contract_is_unchanged(tmp_path) -> None:
    """第 1 期只采集,不动对外契约。展示接口的形状必须一模一样。

    采集库表就建在同一个 sqlite 里,所以「建了表会不会串到展示路径上」要真跑一次。
    """
    app = create_app(Settings(storage_dir=tmp_path))
    Database(Settings(storage_dir=tmp_path).database_path).initialize()

    class StubFetcher:
        def latest(self, page: int = 1):
            return RoughcastRentalFeed(items=(), updated_at="2026-08-20T03:00:00+00:00",
                                       page=page, has_more=False)

    app.state.roughcast_rental_fetcher = StubFetcher()
    with TestClient(app) as client:
        response = client.get("/api/v1/display/roughcast-rentals")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "updated_at": "2026-08-20T03:00:00+00:00",
        "page": 1,
        "has_more": False,
    }


def test_schema_creation_is_idempotent(tmp_path) -> None:
    database = Database(tmp_path / "store_media.sqlite3")
    database.initialize()
    database.initialize()

    with database.connect() as db:
        tables = {row["name"] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'roughcast_%'"
        )}
    assert tables == {
        "roughcast_crawl_runs",
        "roughcast_crawl_stage",
        "roughcast_crawl_log",
        "roughcast_listing_current",
        "roughcast_listing_snapshot",
        "roughcast_communities",
        "roughcast_community_district",
        "roughcast_bizcircle_district",
        "roughcast_community_reference_snapshot",
        "roughcast_score_runs",
        "roughcast_listing_scores",
        "roughcast_review_views",
    }
