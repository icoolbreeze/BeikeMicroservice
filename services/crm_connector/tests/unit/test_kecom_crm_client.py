from __future__ import annotations

from typing import Any

import pytest

from app.domain.errors import (
    UpstreamChangedError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    ConnectionState,
    Principal,
    ProviderStatus,
    RentalListingFilters,
)
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.kecom_crm_client import (
    KecomCrmClient,
    _build_detail_request,
    _build_search_request,
    _build_whoami_request,
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
        listing_id=None,
        maintainer=None,
        scope="my_maintained",
        districts=(),
        monthly_rent_yuan=None,
        area_sqm=None,
        rooms=(),
        orientations=(),
        tags=(),
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
        tags=["地铁房"],
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
    assert query["tags"] == "地铁房"
    # No listing_id/maintainer -> keys absent, never None.
    assert "delCode" not in query and "maintainUcName" not in query


def test_build_search_request_uses_rental_search_route() -> None:
    request = _build_search_request(_filters(community_keyword="万象城"))
    assert request.route == "rental_listing.search"
    assert request.method == "GET"
    assert request.query["communityKeyword"] == "万象城"
    assert request.body is None


def test_build_detail_request_reuses_search_with_single_id() -> None:
    request = _build_detail_request("RC123456")
    assert request.route == "rental_listing.get_detail"
    assert request.query["delCode"] == "RC123456"
    assert request.query["pageSize"] == 1
    # scope defaults to my_maintained in domain model
    assert request.query["sceneCode"] == "puzu_mix_list_pc"


def test_build_whoami_request_uses_identity_me_route() -> None:
    request = _build_whoami_request()
    assert request.route == "identity.me"
    assert request.method == "GET"
    assert request.query["typeList"] == "2"


# -- response parsing --------------------------------------------------------


def test_parse_listing_maps_upstream_fields_to_minimal_domain() -> None:
    row = {
        "delCode": "RC-1",
        "resblockName": "万象城一期",
        "bedroomAmount": 3, "hallAmount": 1, "bathroomAmount": 1,
        "area": 89.5, "price": 4500, "orientation": ["南"],
    }
    listing = _parse_listing(row, scope="my_maintained")
    assert listing.listing_id == "RC-1"
    assert listing.community == "万象城一期"
    assert listing.layout == "3室1厅1卫"
    assert listing.area_sqm == 89.5
    assert listing.monthly_rent_yuan == 4500.0
    assert listing.orientation == "南"
    assert listing.visible_scope == "my_maintained"


def test_parse_listing_handles_missing_fields_without_crashing() -> None:
    listing = _parse_listing({}, scope="my_maintained")
    assert listing.listing_id == ""
    assert listing.community == ""
    assert listing.layout is None
    assert listing.area_sqm is None
    assert listing.monthly_rent_yuan is None
    assert listing.orientation is None


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


def test_get_rental_listing_detail_returns_first_match() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.get_detail",
        200,
        {
            "code": 100000, "msg": "ok",
            "data": {
                "result": [
                    {"delCode": "RC-1", "resblockName": "万象城一期",
                     "bedroomAmount": 2, "hallAmount": 1, "area": 80.0, "price": 3500},
                ],
                "totalCount": 1,
            },
        },
    )
    client = KecomCrmClient(session)

    listing = client.get_rental_listing_detail("RC-1")

    assert session.calls[0].route == "rental_listing.get_detail"
    assert session.calls[0].query["delCode"] == "RC-1"
    assert listing.listing_id == "RC-1"
    assert listing.community == "万象城一期"


def test_get_detail_raises_changed_when_upstream_returns_empty() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.get_detail", 200,
        {"code": 100000, "msg": "ok", "data": {"result": [], "totalCount": 0}},
    )
    client = KecomCrmClient(session)
    with pytest.raises(UpstreamChangedError):
        client.get_rental_listing_detail("missing-id")


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


def test_main_uses_unconfigured_providers_by_default() -> None:
    from app.infrastructure.settings import Settings
    from app.main import create_app

    app = create_app(Settings())
    # default profile -> unconfigured stubs -> connection_status auth_required
    status = app.state.crm_connector_service.connection_status()
    assert status.state.value == "auth_required"
    assert app.state.crm_credential_store is None


def test_main_wires_real_providers_when_profile_set(tmp_path) -> None:
    from app.infrastructure.settings import Settings
    from app.main import create_app

    settings = Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        bound_employee_principal="employee-1",
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
