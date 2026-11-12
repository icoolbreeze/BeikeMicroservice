from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.application.rental_archive import RentalArchiveRunner
from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.rental_archive_store import RentalArchiveStore
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
        "rental_archive_enabled": True,
        "sale_archive_enabled": False,
        "rental_archive_db_path": str(tmp_path / "rental_archive.sqlite3"),
        "rental_archive_list_delay_min_seconds": 0.0,
        "rental_archive_list_delay_max_seconds": 0.0,
        "rental_archive_detail_delay_min_seconds": 0.0,
        "rental_archive_detail_delay_max_seconds": 0.0,
        "rental_archive_pause_seconds_min": 0.0,
        "rental_archive_pause_seconds_max": 0.0,
        "rental_archive_window_start": "00:00",
        "rental_archive_window_end": "23:59",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _wired_archive_app(session: StubSession, tmp_path, **overrides: object):
    """Build the real app, then reroute the session provider to the stub.

    The archive runner is rebuilt on top of the same stubbed
    ``ConnectorService`` with zero delays and a fixed clock so ``POST /tick``
    is deterministic; the daemon thread started by ``create_app`` is stopped
    so only explicit ticks do work.
    """
    settings = _archive_settings(tmp_path, **overrides)
    app = create_app(settings)
    app.state.crm_rental_archive_runner.stop()
    app.state.crm_session_provider = session
    service = app.state.crm_connector_service
    service._session_provider = session  # type: ignore[attr-defined]
    service._crm_client._session = session  # type: ignore[attr-defined]
    app.state.crm_rental_archive_runner = RentalArchiveRunner(
        settings,
        service,
        RentalArchiveStore(settings.rental_archive_db_path),
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        uniform=lambda low, _high: low,
    )
    return app, TestClient(app)


def _enqueue_search(session: StubSession) -> None:
    session.enqueue(
        "rental_listing.search", 200,
        {
            "code": 100000, "msg": "ok",
            "data": {
                "result": [
                    {"delCode": "RC-1", "resblockName": "万象城一期",
                     "resblockId": 3011054720095,
                     "bizCircleName": "华侨城",
                     "bedroomAmount": 2, "hallAmount": 1, "bathroomAmount": 1,
                     "area": 80.0, "price": 3500,
                     "orientation": ["南"], "title": "合租·万象城一期",
                     "floorLevel": "中", "totalFloor": 32},
                ],
                "totalCount": 1, "totalPage": 1,
            },
        },
    )


def test_status_reports_enabled_archive(tmp_path) -> None:
    session = StubSession()
    _app, client = _wired_archive_app(session, tmp_path)

    response = client.get("/api/v1/archive/rental/status")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["within_window"] is True
    assert body["cursor"]["district_id"] == "510104"  # 锦江 leads the cycle
    assert body["counters"]["request_count"] == 0
    assert body["listing_total"] == 0
    assert body["listing_complete"] == 0
    # status lists crawled districts only (zeros before their first row), so
    # 高新 shows up ahead of its first row and 锦江 heads the rotation; the
    # far-suburban skip list (e.g. 青白江) is out of scope here.
    assert len(body["districts"]) == 14
    assert body["districts"][0]["district_id"] == "510104"
    assert all(row["total"] == 0 for row in body["districts"])
    assert any(row["district_id"] == "990002" for row in body["districts"])
    assert not any(row["district_id"] == "510113" for row in body["districts"])
    assert body["cursor"]["pending_units_count"] == 0
    assert body["cursor"]["extra_filters"] == {}
    assert body["last_run"] is None
    assert session.calls == []  # status is a purely local read


def test_dashboard_page_is_served(tmp_path) -> None:
    _app, client = _wired_archive_app(StubSession(), tmp_path)
    response = client.get("/archive")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "房源归档看板" in response.text
    assert "rental" in response.text
    assert "sale" in response.text
    assert "/api/v1/archive/rental/status" in response.text
    assert "/api/v1/archive/sale/status" in response.text


def test_tick_localizes_a_listing_without_sensitive_fields(tmp_path) -> None:
    session = StubSession()
    _enqueue_search(session)
    _app, client = _wired_archive_app(session, tmp_path)

    body: dict | None = None
    for _attempt in range(3):
        tick = client.post("/api/v1/archive/rental/tick")
        assert tick.status_code == 200
        listings = client.get("/api/v1/archive/rental/listings")
        assert listings.status_code == 200
        body = listings.json()
        items = body.get("items") or []
        if items and items[0].get("detail_status") != "none":
            break

    assert body is not None
    assert body["total"] == 1
    item = body["items"][0]
    assert item["listing_id"] == "RC-1"
    assert item["district_id"] == "510104"
    assert item["is_active"] is True
    # The canned row carries no delType, so no detail route is allowed at all;
    # the listing is still localized (as skipped) and cost zero extra calls.
    assert item["detail_status"] == "skipped_unsupported"
    assert len(session.calls) == 1
    assert session.calls[0].route == "rental_listing.search"

    raw = listings.text
    # follow_total / follow_last_7d are allowed now; floor / address /
    # owner price stay forbidden.
    for forbidden in (
        "具体楼层", "详细地址", "signalFloor", "signal_floor",
        "owner_lowest_price", "houseAddress",
    ):
        assert forbidden not in raw
    assert item["payload"]["list"]["floor_desc"] == "中楼层"
    assert item["payload"]["list"]["total_floors"] == 32

    single = client.get("/api/v1/archive/rental/listings/RC-1")
    assert single.status_code == 200
    assert single.json()["listing_id"] == "RC-1"
    missing = client.get("/api/v1/archive/rental/listings/NOPE")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "CRM_ARCHIVE_LISTING_NOT_FOUND"

    status = client.get("/api/v1/archive/rental/status").json()
    assert status["counters"]["request_count"] == 1
    assert status["listing_total"] == 1
    assert status["last_run"]["status"] == "COMPLETE"


def test_search_request_is_scoped_to_the_district(tmp_path) -> None:
    session = StubSession()
    _enqueue_search(session)
    _app, client = _wired_archive_app(session, tmp_path)

    client.post("/api/v1/archive/rental/tick")

    assert len(session.calls) == 1
    query = session.calls[0].query
    assert query["districtId"] == "510104"
    assert query["relationRange"] == 0  # scope=all → 不限
    assert query["pageIndex"] == 1
    assert query["pageSize"] == 50
    assert "price" not in query  # small district: no band slicing


def test_disabled_archive_reports_disabled_and_makes_no_calls(tmp_path) -> None:
    session = StubSession()
    _app, client = _wired_archive_app(
        session, tmp_path, rental_archive_enabled=False
    )

    status = client.get("/api/v1/archive/rental/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert body["running"] is False

    tick = client.post("/api/v1/archive/rental/tick")
    assert tick.status_code == 200
    assert tick.json() == {
        "did_work": False,
        "skipped_reason": "disabled",
        "detail_listing_id": None,
        "cursor": tick.json()["cursor"],
        "counters": tick.json()["counters"],
    }
    assert session.calls == []
    assert client.get("/api/v1/archive/rental/listings").json()["total"] == 0


def test_default_app_starts_the_archive_thread(tmp_path) -> None:
    app = create_app(Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        qr_login_auto_start=False,
        rental_archive_db_path=str(tmp_path / "rental_archive.sqlite3"),
        sale_archive_enabled=False,
    ))
    runner = app.state.crm_rental_archive_runner
    try:
        assert runner is not None
        assert runner.status().enabled is True
        assert runner.running is True
    finally:
        runner.stop()
    assert runner.running is False
