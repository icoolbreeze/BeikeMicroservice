from fastapi.testclient import TestClient

from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.settings import Settings
from app.main import create_app


class StubSession:
    """READY session that replays canned UpstreamResponses for KecomCrmClient.

    Used to drive the FastAPI app -> ConnectorService -> KecomCrmClient ->
    SessionProvider -> UpstreamResponse -> RentalListingPageResponse chain
    end-to-end without a real CRM upstream or DPAPI credential store.
    """

    def __init__(self) -> None:
        self.calls: list[AuthorizedRequest] = []
        self.responses: list[tuple[str, int, object]] = []

    def status(self) -> ProviderStatus:
        return ProviderStatus(ConnectionState.READY, "stub ready")

    def bound_principal(self) -> Principal | None:
        # No locally-bound identity: force the upstream discovery path.
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


def _wired_app(session: StubSession, tmp_path) -> tuple[object, TestClient]:
    """Build a FastAPI app on the real kecom profile, then swap its
    session provider for a stub. This lets the end-to-end test exercise the
    same router -> service -> KecomCrmClient call chain production uses,
    while pinning the upstream responses deterministically."""
    app = create_app(Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        bound_employee_principal="100000003",
    ))
    # Swap in the stub *after* create_app wired the real KecomSessionProvider
    # and KecomCrmClient. Both ConnectorService (for _require_ready / status)
    # and KecomCrmClient (for authorized_fetch) captured provider references
    # at construction, so we reroute both to the stub.
    app.state.crm_session_provider = session
    service = app.state.crm_connector_service
    service._session_provider = session  # type: ignore[attr-defined]
    service._crm_client._session = session  # type: ignore[attr-defined]
    return app, TestClient(app)


def test_search_wanxiangcheng_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {
            "code": 100000, "msg": "ok",
            "data": {
                "result": [
                    {"delCode": "RC-1", "resblockName": "万象城一期",
                     "bedroomAmount": 2, "hallAmount": 1, "area": 80.0, "price": 3500,
                     "orientation": ["南"]},
                    {"delCode": "RC-2", "resblockName": "万象城二期",
                     "bedroomAmount": 3, "hallAmount": 2, "area": 110.0, "price": 5800,
                     "orientation": ["南北"]},
                ],
                "totalCount": 2, "totalPage": 1,
            },
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/search",
        json={"community_keyword": "万象城", "page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1 and body["page_size"] == 20 and body["has_more"] is False
    assert [item["community"] for item in body["items"]] == ["万象城一期", "万象城二期"]
    assert body["items"][0] == {
        "listing_id": "RC-1", "community": "万象城一期",
        "layout": "2室1厅", "area_sqm": 80.0, "monthly_rent_yuan": 3500.0,
        "orientation": "南", "visible_scope": "my_maintained",
    }
    # The request reached SessionProvider with the documented upstream params.
    assert len(session.calls) == 1
    request = session.calls[0]
    assert request.route == "rental_listing.search"
    assert request.query["communityKeyword"] == "万象城"
    assert request.query["sceneCode"] == "puzu_mix_list_pc"
    assert request.query["relationRange"] == 1
    assert request.query["clientOsType"] == 3


def test_get_detail_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.get_detail", 200,
        {
            "code": 100000, "msg": "ok",
            "data": {
                "result": [
                    {"delCode": "RC-42", "resblockName": "万象城天曜",
                     "bedroomAmount": 3, "hallAmount": 2, "bathroomAmount": 2,
                     "area": 145.0, "price": 9000, "orientation": ["东南"]},
                ],
                "totalCount": 1,
            },
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/RC-42")

    assert response.status_code == 200
    listing = response.json()
    assert listing["listing_id"] == "RC-42"
    assert listing["community"] == "万象城天曜"
    assert listing["layout"] == "3室2厅2卫"
    assert listing["area_sqm"] == 145.0
    assert listing["monthly_rent_yuan"] == 9000.0
    assert listing["orientation"] == "东南"
    assert len(session.calls) == 1
    assert session.calls[0].route == "rental_listing.get_detail"
    assert session.calls[0].query["delCode"] == "RC-42"
    assert session.calls[0].query["pageSize"] == 1


def test_whoami_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "identity.me", 200,
        {"code": 100000, "data": {"ucid": "100000003", "name": "张三"}},
    )
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/crm/me")

    assert response.status_code == 200
    assert response.json() == {"employee_principal": "100000003", "display_name": "张三"}
    assert session.calls[0].route == "identity.me"


def test_upstream_invalid_input_surfaces_as_400_detail(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 100001, "msg": "key列表不能为空", "data": {}},
    )
    app, client = _wired_app(session, tmp_path)

    response = client.post("/api/v1/listings/rental/search", json={})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CRM_UPSTREAM_INVALID_INPUT"


def test_upstream_changed_surfaces_as_502_detail(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 999999, "msg": "unknown", "data": {}},
    )
    app, client = _wired_app(session, tmp_path)

    response = client.post("/api/v1/listings/rental/search", json={})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "CRM_UPSTREAM_CHANGED"

