from __future__ import annotations

from typing import Any

import pytest

from app.domain.errors import (
    UpstreamChangedError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    ConnectionState,
    MapBounds,
    Principal,
    ProviderStatus,
    RentalListingFilters,
    RentalMapBubbleFilters,
    RentalMapSearchFilters,
    RentalMapSuggestionFilters,
)
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.kecom_crm_client import (
    KecomCrmClient,
    _build_detail_request,
    _build_search_request,
    _build_whoami_request,
    _build_map_bubbles_request,
    _build_map_search_request,
    _build_map_suggest_request,
    _parse_map_bubbles,
    _parse_map_page,
    _parse_map_suggestions,
    _parse_listing,
    _parse_page,
    _parse_principal,
    _route_query,
)


class CapturingSession:
    """In-memory SessionProvider that records AuthorizedRequests and replays
    canned UpstreamResponse bodies. Lets us assert on request construction
    without any real HTTP."""

    def __init__(self, ready: bool = True) -> None:
        self._ready = ready
        self.calls: list[AuthorizedRequest] = []
        # Queue of (route, status, body) tuples.
        self.responses: list[tuple[str, int, Any]] = []

    def status(self) -> ProviderStatus:
        return (
            ProviderStatus(ConnectionState.READY, "ready")
            if self._ready
            else ProviderStatus(ConnectionState.AUTH_REQUIRED, "no auth")
        )

    def bound_principal(self) -> Principal | None:
        # No locally-bound identity: force the upstream discovery path.
        return None

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected authorized_fetch for {request.route}")
        route, status, body = self.responses.pop(0)
        assert route == request.route, f"expected route {route}, got {request.route}"
        return UpstreamResponse(status_code=status, body=body)

    def enqueue(self, route: str, status: int, body: Any) -> None:
        self.responses.append((route, status, body))


def _filters(**overrides: Any) -> RentalListingFilters:
    base = RentalListingFilters(
        community_keyword=None,
        resblock_ids=(),
        listing_id=None,
        scope="my_maintained",
        monthly_rent_yuan=None,
        area_sqm=None,
        rooms=(),
        orientations=(),
        page=1,
        page_size=20,
    )
    if not overrides:
        return base
    import dataclasses
    return dataclasses.replace(base, **overrides)


# -- request construction ----------------------------------------------------


def test_route_query_emits_fixed_params_and_maps_filters() -> None:
    query = _route_query(_filters(
        community_keyword="万象城",
        monthly_rent_yuan=(2000, 5000),
        area_sqm=(70, 110),
        rooms=[2, 3],
        orientations=["南", "南北"],
        page=2,
        page_size=10,
    ))
    assert query["pageIndex"] == 2
    assert query["pageSize"] == 10
    assert query["relationRange"] == 1
    assert query["sceneCode"] == "puzu_mix_list_pc"
    assert query["clientOsType"] == 3
    assert query["communityKeyword"] == "万象城"
    assert query["priceMin"] == 2000
    assert query["priceMax"] == 5000
    assert query["areaMin"] == 70
    assert query["areaMax"] == 110
    assert query["bedroomAmount"] == "2,3"
    assert query["orientation"] == "南,南北"
    # No listing_id/maintainer -> keys absent, never None.
    assert "delCode" not in query and "maintainUcName" not in query
    # The dead tags param must never be emitted; labels go via condition_filters.
    assert "tags" not in query


def test_route_query_maps_scope_to_page_native_relation_range() -> None:
    assert _route_query(_filters(scope="my_maintained"))["relationRange"] == 1
    assert _route_query(_filters(scope="shared"))["relationRange"] == 4
    assert _route_query(_filters(scope="role_visible"))["relationRange"] == 9
    # Unknown scope degrades to the default 维护盘 range.
    assert _route_query(_filters(scope="bogus"))["relationRange"] == 1


def test_route_query_maps_exact_community_ids_to_page_native_resblock_id() -> None:
    query = _route_query(_filters(resblock_ids=("1611063740147", "1620035540190520")))

    assert query["resblockId"] == "1611063740147,1620035540190520"
    assert "communityKeyword" not in query


def test_build_search_request_uses_rental_search_route() -> None:
    request = _build_search_request(_filters(community_keyword="万象城"))
    assert request.route == "rental_listing.search"
    assert request.method == "GET"
    assert request.query["communityKeyword"] == "万象城"
    assert request.body is None


def test_build_detail_request_is_direct_detailhead_call() -> None:
    request = _build_detail_request("RC123456")
    assert request.route == "rental_listing.get_detail"
    assert request.method == "GET"
    assert request.query == {"delCode": "RC123456"}
    assert request.body is None


def test_build_whoami_request_uses_identity_me_route() -> None:
    request = _build_whoami_request()
    assert request.route == "identity.me"
    assert request.method == "GET"
    assert request.query["typeList"] == "2"


def _map_bounds() -> MapBounds:
    return MapBounds(104.0, 104.1, 30.6, 30.7)


def _map_search_filters(*, mode: str = "viewport") -> RentalMapSearchFilters:
    return RentalMapSearchFilters(
        city_id="510100", data_source="ZF", bounds=_map_bounds(), page=2,
        mode=mode, condition_tokens=("obrp1800", "oerp2200", "l2"),
        result_type="1", resblock_id=None, resblock_ids=("rb-1", "rb-2"),
    )


def test_map_request_builders_use_allowlisted_routes_and_parameters() -> None:
    viewport = _build_map_search_request(_map_search_filters())
    assert viewport.route == "rental_map.search"
    assert viewport.query["condition"] == "obrp1800oerp2200l2"
    assert viewport.query["minLongitude"] == 104.0

    circle = _build_map_search_request(_map_search_filters(mode="circle"))
    assert circle.route == "rental_map.search_circle"
    assert circle.query["resblockIds"] == "rb-1,rb-2"

    bubbles = _build_map_bubbles_request(RentalMapBubbleFilters(
        city_id="510100", data_source="ZF", bounds=_map_bounds(),
        group_type="community", group_id=None, condition_tokens=("l2",),
    ))
    assert bubbles.route == "rental_map.bubbles"
    assert bubbles.query["groupType"] == "community"

    suggest = _build_map_suggest_request(RentalMapSuggestionFilters(
        city_id="510100", data_source="ZF", query="万象城",
    ))
    assert suggest.route == "rental_map.suggest"
    assert suggest.query["pageSize"] == 30


def test_map_parsers_map_list_bubbles_and_suggestions() -> None:
    page = _parse_map_page(
        {"code": 0, "data": {"list": [{"delCode": "RC-1", "title": "万象城套二",
        "desc": "2室1厅", "tags": ["近地铁"], "priceStr": "2000元/月"}], "total": 1}},
        _map_search_filters(), "map-request",
    )
    assert page.items[0].listing_id == "RC-1"
    assert page.items[0].tags == ("近地铁",)

    bubbles = _parse_map_bubbles(
        {"code": 0, "data": {"bubbleList": [{"id": "rb-1", "name": "万象城一期",
        "latitude": 30.65, "longitude": 104.1, "count": 9}]}}, "community",
    )
    assert bubbles[0].bubble_id == "rb-1"
    assert bubbles[0].latitude == 30.65

    suggestions = _parse_map_suggestions(
        {"code": 0, "data": {"list": [{"itemType": "bizcircle", "itemId": "biz-1",
        "itemName": "万象城", "pointLat": 30.65, "pointLng": 104.1}]}},
    )
    assert suggestions[0].name == "万象城"


def test_map_listing_extracts_id_from_action_url_when_no_id_field() -> None:
    # The live drawhouselist rows carry no delCode/houseId/id; the only
    # identifier is the trailing path segment of actionUrl.
    page = _parse_map_page(
        {"code": 0, "data": {"list": [{
            "actionUrl": "https://trusteeship.link.lianjia.com/house/detail/10611245074901",
            "title": "整租·三街坊社区 2室1厅",
            "desc": "新华公园/54m²/2室1厅/东北",
            "priceStr": "2150元/月",
        }], "total": 1}},
        _map_search_filters(), "map-request",
    )
    assert page.items[0].listing_id == "10611245074901"


def test_map_listing_prefers_explicit_id_over_action_url() -> None:
    page = _parse_map_page(
        {"code": 0, "data": {"list": [{
            "delCode": "RC-1",
            "actionUrl": "https://trusteeship.link.lianjia.com/house/detail/OTHER-9",
            "title": "整租·万象城套二",
        }], "total": 1}},
        _map_search_filters(), "map-request",
    )
    assert page.items[0].listing_id == "RC-1"


def test_map_listing_id_stays_empty_without_any_source() -> None:
    page = _parse_map_page(
        {"code": 0, "data": {"list": [{"title": "整租·无名房源"}]}},
        _map_search_filters(), "map-request",
    )
    assert page.items[0].listing_id == ""


# -- response parsing --------------------------------------------------------


def test_parse_listing_maps_upstream_fields_to_minimal_domain() -> None:
    row = {
        "delCode": "RC-1",
        "resblockName": "万象城一期",
        "bedroomAmount": 3, "hallAmount": 1, "bathroomAmount": 1,
        "area": 89.5, "price": 4500, "orientation": ["南"],
        "delType": 2,
    }
    listing = _parse_listing(row, scope="my_maintained")
    assert listing.listing_id == "RC-1"
    assert listing.community == "万象城一期"
    assert listing.layout == "3室1厅1卫"
    assert listing.area_sqm == 89.5
    assert listing.monthly_rent_yuan == 4500.0
    assert listing.orientation == "南"
    assert listing.visible_scope == "my_maintained"
    # delType distinguishes 普租 (2) from 托管 (5); detailHead only serves 普租.
    assert listing.del_type == 2


def test_parse_listing_exposes_trusteeship_del_type() -> None:
    listing = _parse_listing({"delType": 5}, scope="my_maintained")
    assert listing.del_type == 5


def test_parse_listing_handles_missing_fields_without_crashing() -> None:
    listing = _parse_listing({}, scope="my_maintained")
    assert listing.listing_id == ""
    assert listing.community == ""
    assert listing.layout is None
    assert listing.area_sqm is None
    assert listing.monthly_rent_yuan is None
    assert listing.orientation is None
    assert listing.del_type is None


def test_parse_page_returns_paged_domain_with_has_more() -> None:
    body = {
        "code": 100000, "msg": "ok",
        "data": {
            "result": [
                {"delCode": "RC-1", "resblockName": "万象城一期",
                 "bedroomAmount": 2, "hallAmount": 1, "area": 80.0, "price": 3000,
                 "orientation": ["南北"]},
                {"delCode": "RC-2", "resblockName": "万象城二期",
                 "bedroomAmount": 3, "hallAmount": 2, "area": 110.0, "price": 5000,
                 "orientation": ["南"]},
            ],
            "totalCount": 25, "totalPage": 2,
        },
    }
    page = _parse_page(body, _filters(page=1, page_size=2), request_id="req-1")
    assert [item.listing_id for item in page.items] == ["RC-1", "RC-2"]
    assert [item.community for item in page.items] == ["万象城一期", "万象城二期"]
    assert page.page == 1 and page.page_size == 2
    assert page.has_more is True  # 1*2 < 25
    assert page.request_id == "req-1"


def test_parse_page_marks_no_more_at_last_page() -> None:
    body = {"code": 100000, "data": {"result": [], "totalCount": 20}}
    page = _parse_page(body, _filters(page=2, page_size=20), request_id="req-2")
    assert page.has_more is False


def test_parse_principal_reads_ucid_from_data() -> None:
    principal = _parse_principal({"code": 100000, "data": {"ucid": "100000003", "name": "张三"}})
    assert principal.employee_principal == "100000003"
    assert principal.display_name == "张三"


def test_parse_principal_raises_when_principal_missing() -> None:
    with pytest.raises(UpstreamChangedError):
        _parse_principal({"code": 100000, "data": {}})


# -- end-to-end via KecomCrmClient -------------------------------------------


def test_search_rental_listings_through_session_boundary() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.search",
        200,
        {
            "code": 100000, "msg": "ok",
            "data": {
                "result": [
                    {"delCode": "RC-1", "resblockName": "万象城一期",
                     "bedroomAmount": 2, "hallAmount": 1, "area": 80.0, "price": 3500,
                     "orientation": ["南"]},
                ],
                "totalCount": 1, "totalPage": 1,
            },
        },
    )
    client = KecomCrmClient(session)

    page = client.search_rental_listings(_filters(community_keyword="万象城"))

    assert len(session.calls) == 1
    request = session.calls[0]
    assert request.route == "rental_listing.search"
    assert request.query["communityKeyword"] == "万象城"
    assert [item.community for item in page.items] == ["万象城一期"]
    assert page.items[0].monthly_rent_yuan == 3500.0


def test_get_rental_listing_detail_parses_detail_head() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.get_detail",
        200,
        {
            "code": 100000, "msg": "加载成功",
            "data": {
                "delCode": 106128814453, "resblockName": "双桥路南一街",
                "bedroomAmount": 2, "livingroomAmount": 1, "bathroomAmount": 1,
                "houseArea": 51.23, "housePrice": 1350, "oriented": ["南"],
                "resblockId": 16000000145204, "houseGrade": "B",
            },
        },
    )
    client = KecomCrmClient(session)

    listing = client.get_rental_listing_detail("106128814453")

    assert session.calls[0].route == "rental_listing.get_detail"
    assert session.calls[0].query == {"delCode": "106128814453"}
    assert listing.listing_id == "106128814453"
    assert listing.community == "双桥路南一街"
    assert listing.layout == "2室1厅1卫"
    assert listing.area_sqm == 51.23
    assert listing.monthly_rent_yuan == 1350
    assert listing.orientation == "南"
    assert listing.visible_scope == "detail"


def test_get_detail_raises_invalid_input_when_data_empty() -> None:
    # Empty data is the upstream's explicit "no such listing" answer
    # (e.g. trusteeship-domain ids that do not exist in the 普租 domain);
    # it must fail loudly instead of falling back to a wrong house.
    session = CapturingSession()
    session.enqueue(
        "rental_listing.get_detail", 200,
        {"code": 100000, "msg": "加载成功", "data": {}},
    )
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamInvalidInputError):
        client.get_rental_listing_detail("10611245074901")


def test_business_code_100001_maps_to_invalid_input() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 100001, "msg": "key列表不能为空", "data": {}},
    )
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamInvalidInputError) as exc_info:
        client.search_rental_listings(_filters())
    assert exc_info.value.code == "CRM_UPSTREAM_INVALID_INPUT"


def test_unknown_business_code_maps_to_upstream_changed() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 999999, "msg": "unexpected", "data": {}},
    )
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamChangedError):
        client.search_rental_listings(_filters())


def test_non_200_status_maps_to_upstream_changed() -> None:
    session = CapturingSession()
    session.enqueue("rental_listing.search", 500, {"code": 0, "msg": "boom"})
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamChangedError) as exc_info:
        client.search_rental_listings(_filters())
    assert "status 500" in str(exc_info.value)


def test_non_object_body_maps_to_upstream_changed() -> None:
    session = CapturingSession()
    session.enqueue("rental_listing.search", 200, ["not", "an", "object"])
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamChangedError):
        client.search_rental_listings(_filters())


def test_whoami_routes_through_identity_me() -> None:
    session = CapturingSession()
    session.enqueue("identity.me", 200, {"code": 100000, "data": {"ucid": "1000001", "name": "李四"}})
    client = KecomCrmClient(session)

    principal = client.whoami()

    assert session.calls[0].route == "identity.me"
    assert principal == Principal(employee_principal="1000001", display_name="李四")


# -- integration: main.py wiring ---------------------------------------------


def test_main_uses_unconfigured_providers_when_profile_forced() -> None:
    from app.infrastructure.settings import Settings
    from app.main import create_app

    app = create_app(Settings(
        upstream_profile="unconfigured",
        qr_login_auto_start=False,
    ))
    # unconfigured profile -> unconfigured stubs -> connection_status auth_required
    status = app.state.crm_connector_service.connection_status()
    assert status.state.value == "auth_required"
    assert app.state.crm_credential_store is None


def test_main_defaults_to_real_profile(tmp_path) -> None:
    from app.infrastructure.settings import Settings
    from app.main import create_app

    settings = Settings(
        credential_store_path=str(tmp_path / "cred.bin"),
        qr_login_auto_start=False,
    )
    assert settings.upstream_profile == "kecom-prod"
    app = create_app(settings)
    from app.infrastructure.kecom_session_provider import KecomSessionProvider

    assert isinstance(app.state.crm_session_provider, KecomSessionProvider)
    assert app.state.crm_qr_login_manager is not None


def test_main_wires_real_providers_when_profile_set(tmp_path) -> None:
    from app.infrastructure.settings import Settings
    from app.main import create_app

    settings = Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        bound_employee_principal="employee-1",
        qr_login_auto_start=False,
    )
    app = create_app(settings)

    from app.infrastructure.kecom_crm_client import KecomCrmClient
    from app.infrastructure.kecom_session_provider import KecomSessionProvider

    assert isinstance(app.state.crm_session_provider, KecomSessionProvider)
    assert isinstance(app.state.crm_connector_service._crm_client, KecomCrmClient)
    assert app.state.crm_credential_store is not None
    # No active credential yet -> still auth_required, but now from a real
    # session provider reading an empty DPAPI store rather than the stub.
    status = app.state.crm_connector_service.connection_status()
    assert status.state.value == "auth_required"


def test_wired_app_search_returns_auth_required_when_no_credential(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.infrastructure.settings import Settings
    from app.main import create_app

    settings = Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        bound_employee_principal="employee-1",
        qr_login_auto_start=False,
    )
    app = create_app(settings)
    # Without bootstrap, a wired app must still refuse search with the same
    # structured error code the unconfigured profile returns, so the FastAPI
    # contract holds across profiles.
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/listings/rental/search",
            json={"community_keyword": "万象城"},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "CRM_AUTH_REQUIRED"
