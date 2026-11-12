from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.application.sale_archive import SaleArchiveRunner
from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.sale_archive_store import SaleArchiveStore
from app.infrastructure.settings import Settings
from app.main import create_app

NOW = datetime(2026, 9, 9, 6, 0, tzinfo=UTC)  # 14:00 Asia/Shanghai


class StubSession:
    """READY session replaying canned UpstreamResponses (see test_api.py)."""

    def __init__(self) -> None:
        self.calls: list[AuthorizedRequest] = []
        self.responses: list[tuple[str, int, object]] = []
        self.ready = True

    def status(self) -> ProviderStatus:
        if not self.ready:
            return ProviderStatus(ConnectionState.AUTH_REQUIRED, "stub unauthenticated")
        return ProviderStatus(ConnectionState.READY, "stub ready")

    def bound_principal(self) -> Principal | None:
        return None

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected fetch for {request.route}")
        route, status, body = self.responses.pop(0)
        assert route == request.route
        return UpstreamResponse(status_code=status, body=body)

    def enqueue(self, route: str, status: int, body: object) -> None:
        self.responses.append((route, status, body))


def _archive_settings(tmp_path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "upstream_profile": "kecom-prod",
        "credential_store_path": str(tmp_path / "cred.bin"),
        "bound_employee_principal": "100000003",
        "qr_login_auto_start": False,
        # Rental stays off: this suite exercises the sale runner only.
        "rental_archive_enabled": False,
        "sale_archive_enabled": True,
        "sale_archive_db_path": str(tmp_path / "sale_archive.sqlite3"),
        "sale_archive_list_delay_min_seconds": 0.0,
        "sale_archive_list_delay_max_seconds": 0.0,
        "sale_archive_detail_delay_min_seconds": 0.0,
        "sale_archive_detail_delay_max_seconds": 0.0,
        "sale_archive_pause_seconds_min": 0.0,
        "sale_archive_pause_seconds_max": 0.0,
        "sale_archive_window_start": "00:00",
        "sale_archive_window_end": "23:59",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _wired_archive_app(session: StubSession, tmp_path, **overrides: object):
    """Build the real app, then reroute the session provider to the stub.

    The sale runner is rebuilt on top of the same stubbed
    ``ConnectorService`` with zero delays and a fixed clock so ``POST /tick``
    is deterministic; the daemon thread started by ``create_app`` is stopped
    so only explicit ticks do work.
    """
    settings = _archive_settings(tmp_path, **overrides)
    app = create_app(settings)
    app.state.crm_sale_archive_runner.stop()
    app.state.crm_session_provider = session
    service = app.state.crm_connector_service
    service._session_provider = session  # type: ignore[attr-defined]
    service._crm_client._session = session  # type: ignore[attr-defined]
    app.state.crm_sale_archive_runner = SaleArchiveRunner(
        settings,
        service,
        SaleArchiveStore(settings.sale_archive_db_path),
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        uniform=lambda low, _high: low,
    )
    return app, TestClient(app)


def _enqueue_search(session: StubSession) -> None:
    session.enqueue(
        "sale_listing.search", 200,
        {
            "code": 1, "msg": "ok",
            "data": {
                "list": [
                    {"houseDelCode": "SC-1", "communityName": "万象城一期",
                     "communityId": "3011054720095",
                     "bizCircleName": "东湖",
                     "unitType": "2室1厅1卫", "areaSize": 80.0,
                     "totalPrice": 1200000, "totalPriceStr": "120万",
                     "unitPrice": 15000,
                     "floorType": "中楼层", "totalFloor": 18,
                     "delType": 1, "qualityScore": 88.5,
                     "surfaceImage": "https://img.ljcdn.com/a.jpg"},
                ],
                "totalCount": 1, "currentPage": 1, "totalPage": 1,
            },
        },
    )


def _enqueue_detail(session: StubSession) -> None:
    """Head(views+ext) + maintain + follows + comment + owner + score."""
    session.enqueue(
        "sale_listing.get_detail", 200,
        {
            "code": 1, "msg": "ok",
            "data": {
                "housedelBaseInfo": {
                    "displayName": "万象城一期", "displayPrice": "120",
                    "area": 80.0, "displayFloor": "中楼层(共18层)",
                    "ownerReservePrice": "120万",
                    "holderInfo": {"name": "李四", "orgName": "东湖门店"},
                },
                "basicInfo": {"districtName": "锦江", "bizcircleName": "东湖"},
            },
        },
    )
    session.enqueue(
        "sale_listing.get_ext_info", 200,
        {"code": 1, "msg": "ok", "data": {"netWorkStatus": 1}},
    )
    session.enqueue(
        "sale_listing.get_maintain_info", 200,
        {
            "code": 1, "msg": "ok",
            "data": {
                "maintainBasicInfo": {
                    "completeRate": "9/9",
                    "maintainList": [
                        {
                            "name": "看房信息",
                            "row": [
                                {
                                    "list": [
                                        {"key": "ownerPhone", "name": "业主电话",
                                         "value": "13800001111",
                                         "important": False},
                                        {"key": "visit_time", "name": "看房时间",
                                         "value": "周末", "important": True},
                                    ],
                                },
                            ],
                        },
                    ],
                },
            },
        },
    )
    session.enqueue(
        "sale_listing.get_follow", 200,
        {
            "code": 1, "msg": "ok",
            "data": {
                "list": [
                    {"id": 1, "followContent": "客户已看房，有意向",
                     "creatorName": "张三 13912345678",
                     "createTime": "2026-09-01 10:00", "onTop": False},
                ],
            },
        },
    )
    session.enqueue(
        "sale_listing.get_agent_comment", 200,
        {
            "code": 1, "msg": "恭喜你，操作成功",
            "data": {
                "commentStatus": "50",
                "commentTitle": "一句话卖点",
                "commentDesc": "审核通过，外网可见",
                "commentTime": "2026-10-18 21:15:05",
                "detailList": [
                    {"commentTitle": "核心卖点", "commentType": "sellPoint",
                     "commentContent": "交通方便，联系 13700001111"},
                    {"commentTitle": "周边配套", "commentType": "mate",
                     "commentContent": "学校商场"},
                ],
            },
        },
    )
    session.enqueue(
        "sale_listing.get_owner_recommend", 200,
        {"code": 1, "msg": "对不起，没有找到相关数据", "data": None},
    )
    session.enqueue(
        "sale_listing.get_score", 200,
        {
            "code": 1, "msg": "恭喜你，操作成功",
            "data": {
                "score": 8.27, "isHaofang": 0, "quotaScore": 0,
                "quotaScoreLevel": 0,
                "radarDimVoList": [
                    {"title": "客户热度", "rule": "do not store",
                     "priceDimList": [{"value": "带看量6，排名第1", "bold": True}]},
                    {"title": "业主意愿", "priceDimList": [{"value": "急售"}]},
                ],
                "houseRadarDimScore": {
                    "quotaScore": 1.0, "priceDimScore": 2.0, "custDimScore": 3.0,
                    "marketDimScore": 4.0, "ownerDimScore": 5.0, "houseDimScore": 6.0,
                },
            },
        },
    )


def test_status_reports_enabled_archive(tmp_path) -> None:
    session = StubSession()
    _app, client = _wired_archive_app(session, tmp_path)

    response = client.get("/api/v1/archive/sale/status")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["within_window"] is True
    assert body["cursor"]["district_id"] == "510104"  # 锦江 leads the cycle
    assert body["counters"]["request_count"] == 0
    assert body["listing_total"] == 0
    assert body["listing_complete"] == 0
    # The 14 crawled districts, in fixed order, zeros included.
    assert len(body["districts"]) == 14
    assert [row["district_id"] for row in body["districts"]] == [
        "510104", "510105", "510106", "510107", "510108",
        "510112", "510122", "510115", "510114", "510124",
        "23008850", "23009121", "990002", "23009222",
    ]
    assert all(row["total"] == 0 for row in body["districts"])
    assert body["cursor"]["pending_units_count"] == 0
    assert body["cursor"]["extra_filters"] == {}
    assert body["last_run"] is None
    assert session.calls == []  # status is a purely local read


def test_dashboard_page_is_served(tmp_path) -> None:
    _app, client = _wired_archive_app(StubSession(), tmp_path)
    response = client.get("/archive")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "买卖" in response.text
    assert "/api/v1/archive/sale/status" in response.text


def test_tick_localizes_a_listing_without_sensitive_fields(tmp_path) -> None:
    session = StubSession()
    _enqueue_search(session)
    _enqueue_detail(session)
    _app, client = _wired_archive_app(session, tmp_path)

    client.post("/api/v1/archive/sale/tick")  # list page, queues detail
    tick = client.post("/api/v1/archive/sale/tick")  # localized detail
    assert tick.status_code == 200
    assert tick.json()["detail_listing_id"] == "SC-1"

    listings = client.get("/api/v1/archive/sale/listings")
    assert listings.status_code == 200
    body = listings.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["listing_id"] == "SC-1"
    assert item["district_id"] == "510104"
    assert item["is_active"] is True
    assert item["detail_status"] == "complete"

    raw = listings.text
    # Floor / address / owner price stay forbidden; coarse floor survives.
    for forbidden in (
        "具体楼层", "详细地址", "signalFloor", "signal_floor",
        "owner_reserve_price", "ownerReservePrice", "houseAddress",
        "13800001111", "13912345678", "13700001111",
    ):
        assert forbidden not in raw
    assert item["payload"]["list"]["floor_desc"] == "中楼层/18"
    # Three localized sections all stored.
    detail = item["payload"]["detail"]
    assert detail["head"]["display_floor"] == "中楼层(共18层)"
    assert detail["maintain"]["modules"][0]["name"] == "看房信息"
    assert detail["follows"][0]["content"] == "客户已看房，有意向"
    assert detail["agent_comment"]["comment_status"] == "50"
    assert detail["agent_comment"]["sections"][0]["title"] == "核心卖点"
    assert detail["agent_comment"]["sections"][0]["content"] == "交通方便，联系 "
    assert "rule" not in json.dumps(detail["score"])
    assert detail["score"]["sections"][0]["title"] == "客户热度"
    assert detail["score"]["sections"][0]["lines"] == ["带看量6，排名第1"]
    assert detail["score"]["sections"][1]["title"] == "业主意愿"
    assert detail["score"]["is_haofang"] is False
    assert detail["owner_recommend"] == {"content": None, "picture_urls": []}

    single = client.get("/api/v1/archive/sale/listings/SC-1")
    assert single.status_code == 200
    assert single.json()["listing_id"] == "SC-1"
    missing = client.get("/api/v1/archive/sale/listings/NOPE")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "CRM_ARCHIVE_LISTING_NOT_FOUND"

    status = client.get("/api/v1/archive/sale/status").json()
    # 1 list request + head(2) + maintain + follows + comment + owner + score.
    assert status["counters"]["request_count"] == 8
    assert status["listing_total"] == 1
    assert status["listing_complete"] == 1
    assert status["last_run"]["status"] == "COMPLETE"


def test_search_request_is_scoped_to_the_district(tmp_path) -> None:
    session = StubSession()
    _enqueue_search(session)
    _enqueue_detail(session)
    _app, client = _wired_archive_app(session, tmp_path)

    client.post("/api/v1/archive/sale/tick")

    assert len(session.calls) == 1
    query = session.calls[0].query
    assert query["disId"] == "510104"
    assert query["vertical"] == "all"  # 不限, never gdiv_mt
    assert query["currentPage"] == 1
    assert "pageSize" not in query  # upstream fixes 30 rows; do not send it
    assert "price" not in query  # small district: no band slicing
    assert "delType" not in query  # sale has no 普租/托管 split


def test_disabled_archive_reports_disabled_and_makes_no_calls(tmp_path) -> None:
    session = StubSession()
    _app, client = _wired_archive_app(
        session, tmp_path, sale_archive_enabled=False
    )

    status = client.get("/api/v1/archive/sale/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert body["running"] is False

    tick = client.post("/api/v1/archive/sale/tick")
    assert tick.status_code == 200
    assert tick.json() == {
        "did_work": False,
        "skipped_reason": "disabled",
        "detail_listing_id": None,
        "cursor": tick.json()["cursor"],
        "counters": tick.json()["counters"],
    }
    assert session.calls == []
    assert client.get("/api/v1/archive/sale/listings").json()["total"] == 0


def test_default_app_starts_the_archive_thread(tmp_path) -> None:
    app = create_app(Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        qr_login_auto_start=False,
        rental_archive_enabled=False,
        sale_archive_db_path=str(tmp_path / "sale_archive.sqlite3"),
    ))
    runner = app.state.crm_sale_archive_runner
    try:
        assert runner is not None
        assert runner.status().enabled is True
        assert runner.running is True
    finally:
        runner.stop()
    assert runner.running is False
