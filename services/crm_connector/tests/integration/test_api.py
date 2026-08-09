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
        qr_login_auto_start=False,
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


def test_search_accepts_multiple_exact_community_ids(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {
            "code": 100000,
            "data": {
                "result": [],
                "totalCount": 0,
                "totalPage": 1,
            },
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/search",
        json={
            "resblock_ids": ["1611063740147", "1620035540190520"],
            "page": 1,
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    assert session.calls[0].query["resblockId"] == "1611063740147,1620035540190520"
    assert "communityKeyword" not in session.calls[0].query


def test_get_detail_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.get_detail", 200,
        {
            "code": 100000, "msg": "加载成功",
            "data": {
                "delCode": 106128814453, "resblockName": "双桥路南一街",
                "bedroomAmount": 3, "livingroomAmount": 2, "bathroomAmount": 2,
                "houseArea": 145.0, "housePrice": 9000, "oriented": ["东南"],
            },
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/RC-42")

    assert response.status_code == 200
    listing = response.json()
    # listing_id comes from the upstream's authoritative delCode
    assert listing["listing_id"] == "106128814453"
    assert listing["community"] == "双桥路南一街"
    assert listing["layout"] == "3室2厅2卫"
    assert listing["area_sqm"] == 145.0
    assert listing["monthly_rent_yuan"] == 9000.0
    assert listing["orientation"] == "东南"
    assert len(session.calls) == 1
    assert session.calls[0].route == "rental_listing.get_detail"
    assert session.calls[0].query == {"delCode": "RC-42"}


def test_listing_filter_options_and_native_conditions_flow_through_api(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.filter_options", 200,
        {"code": 100000, "data": [{
            "key": "rentType", "name": "租赁方式", "type": "select",
            "value": None, "defaultValue": None,
            "children": [
                {"key": None, "name": "整租", "type": "select", "value": "001", "children": []},
                {"key": None, "name": "合租", "type": "select", "value": "002", "children": []},
            ],
        }]},
    )
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 100000, "data": {"result": [], "totalCount": 0}},
    )
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 100000, "data": {"result": [], "totalCount": 0}},
    )
    _app, client = _wired_app(session, tmp_path)

    options = client.get("/api/v1/listings/rental/filter-options")
    search = client.post(
        "/api/v1/listings/rental/search",
        json={"condition_filters": {
            "rentType": "002", "bedroomAmount": 2,
            "orientation": ["100500000003", "100500000001"],
            "price": "0:3000",
        }},
    )
    budget_search = client.post(
        "/api/v1/listings/rental/search",
        json={"condition_filters": {"rentType": "002"}, "budget_yuan": 2000},
    )

    assert options.status_code == 200
    assert options.json()[0]["key"] == "rentType"
    assert options.json()[0]["children"][1]["value"] == "002"
    assert search.status_code == 200
    assert session.calls[1].query["rentType"] == "002"
    assert session.calls[1].query["bedroomAmount"] == 2
    assert session.calls[1].query["orientation"] == "100500000003,100500000001"
    assert session.calls[1].query["price"] == "0:3000"
    assert budget_search.status_code == 200
    assert session.calls[2].query["price"] == "0:2500"


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


def test_low_level_viewport_and_bubble_routes_are_not_exposed(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_map.bubbles", 200,
        {"code": 0, "data": {"bubbleList": [{
            "id": "rb-1", "name": "sample", "latitude": 30.65, "longitude": 104.1,
        }]}},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/map/bubbles",
        json={
            "bounds": {
                "min_longitude": 104.0, "max_longitude": 104.2,
                "min_latitude": 30.5, "max_latitude": 30.8,
            },
            "group_type": "community",
            "rooms": [2, 3],
            "rental_modes": ["whole_rent"],
        },
    )

    assert response.status_code == 404
    response = client.post("/api/v1/listings/rental/map/search", json={})
    assert response.status_code == 404
    assert session.calls == []


def test_nearby_shared_rent_omits_unspecified_lower_price_bound(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_map.bubbles", 200,
        {"code": 0, "data": {"bubbleList": [{
            "id": "rb-1", "name": "sample", "latitude": 30.6545, "longitude": 104.1221,
        }]}},
    )
    session.enqueue(
        "rental_map.search_circle", 200,
        {"code": 0, "data": {"list": [], "total": 0}},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/map/nearby",
        json={
            "location": "sample", "center_latitude": 30.654406,
            "center_longitude": 104.122005, "radius_meters": 1000,
            "price_max_yuan": 2200, "rental_modes": ["shared_rent"],
        },
    )

    assert response.status_code == 200
    assert session.calls[0].query["condition"] == "oerp2200rt002"
    assert session.calls[1].query["condition"] == "oerp2200rt002"


def test_nearby_map_search_resolves_location_then_uses_community_ids(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_map.suggest", 200,
        {"code": 0, "data": {"list": [{
            "itemType": "bizcircle", "itemId": "biz-wxc", "itemName": "万象城",
            "pointLat": 30.65, "pointLng": 104.1,
        }]}},
    )
    session.enqueue(
        "rental_map.bubbles", 200,
        {"code": 0, "data": {"bubbleList": [
            {"id": "rb-near", "name": "万象城一期", "latitude": 30.651, "longitude": 104.101},
            {"id": "rb-far", "name": "远处小区", "latitude": 30.67, "longitude": 104.1},
        ]}},
    )
    session.enqueue(
        "rental_map.search_circle", 200,
        {"code": 0, "data": {"list": [{
            "delCode": "RC-map-1", "title": "万象城附近套二", "desc": "2室1厅",
            "priceStr": "2000元/月",
        }], "total": 1}},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/map/nearby",
        json={
            "location": "万象城", "radius_meters": 1000,
            "price_min_yuan": 1800, "price_max_yuan": 2200, "rooms": [2],
            "rental_modes": ["whole_rent"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["center"]["name"] == "万象城"
    assert body["matched_community_count"] == 1
    assert body["approximation"] == "community_centroid"
    assert body["result"]["items"][0]["listing_id"] == "RC-map-1"
    assert [call.route for call in session.calls] == [
        "rental_map.suggest", "rental_map.bubbles", "rental_map.search_circle",
    ]
    assert session.calls[2].query["resblockIds"] == "rb-near"
    assert session.calls[2].query["condition"] == "obrp1800oerp2200l2rt001"


def test_nearby_map_search_accepts_a_pre_resolved_center(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_map.bubbles", 200,
        {"code": 0, "data": {"bubbleList": [{
            "id": "rb-near", "name": "万象城一期", "latitude": 30.6545, "longitude": 104.1221,
        }]}},
    )
    session.enqueue(
        "rental_map.search_circle", 200,
        {"code": 0, "data": {"list": [{
            "delCode": "RC-map-2", "title": "万象城附近", "desc": "2室1厅",
        }], "total": 1}},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/map/nearby",
        json={
            "location": "万象城", "center_latitude": 30.654406,
            "center_longitude": 104.122005, "radius_meters": 1000,
        },
    )

    assert response.status_code == 200
    assert response.json()["center"]["item_type"] == "provided_coordinate"
    assert [call.route for call in session.calls] == [
        "rental_map.bubbles", "rental_map.search_circle",
    ]

