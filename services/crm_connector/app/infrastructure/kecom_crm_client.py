from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from app.domain.errors import (
    UpstreamChangedError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    Principal,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
)
from app.domain.providers.crm_client import CrmClient as CrmClientProtocol
from app.domain.providers.session_provider import (
    AuthorizedRequest,
    SessionProvider,
)

logger = logging.getLogger(__name__)

# Business envelope codes documented in docs/crm-auth-flow-analysis.md §4.
_CODE_OK = 100000
_CODE_INVALID_INPUT = 100001
_CODE_UNAUTHORIZED = 403

# Fixed upstream query parameters documented in §4 "GET /api/houseList/search/pc/list".
_SEARCH_SCENE_CODE = "puzu_mix_list_pc"
_SEARCH_CLIENT_OS = 3
_SEARCH_RELATION_RANGE_MY = 1


def _route_query(filters: RentalListingFilters) -> dict[str, str | int]:
    """Map RentalListingFilters to the upstream search query string.

    Only documented params are emitted; unknown filters are dropped rather
    than smuggled to the upstream. ``community_keyword`` becomes the
    upstream ``communityKeyword`` (case-sensitive on the CRM side).
    """
    params: dict[str, str | int] = {
        "pageIndex": filters.page,
        "pageSize": filters.page_size,
        "relationRange": _SEARCH_RELATION_RANGE_MY,
        "sceneCode": _SEARCH_SCENE_CODE,
        "clientOsType": _SEARCH_CLIENT_OS,
    }
    if filters.community_keyword:
        params["communityKeyword"] = filters.community_keyword
    if filters.listing_id:
        params["delCode"] = filters.listing_id
    if filters.maintainer:
        params["maintainUcName"] = filters.maintainer
    if filters.districts:
        params["districts"] = ",".join(filters.districts)
    if filters.monthly_rent_yuan is not None:
        lo, hi = filters.monthly_rent_yuan
        if lo is not None:
            params["priceMin"] = int(lo)
        if hi is not None:
            params["priceMax"] = int(hi)
    if filters.area_sqm is not None:
        lo, hi = filters.area_sqm
        if lo is not None:
            params["areaMin"] = int(lo)
        if hi is not None:
            params["areaMax"] = int(hi)
    if filters.rooms:
        params["bedroomAmount"] = ",".join(str(r) for r in filters.rooms)
    if filters.orientations:
        params["orientation"] = ",".join(filters.orientations)
    if filters.tags:
        params["tags"] = ",".join(filters.tags)
    return params


def _build_search_request(filters: RentalListingFilters) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="rental_listing.search",
        method="GET",
        query=_route_query(filters),
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_detail_request(listing_id: str) -> AuthorizedRequest:
    # rental_listing.get_detail reuses the search endpoint with delCode until
    # the dedicated upstream route is captured (docs §8.4). We restrict the
    # upstream call to a single-listing query so the result is predictable.
    filters = RentalListingFilters(
        community_keyword=None,
        listing_id=listing_id,
        maintainer=None,
        scope="my_maintained",
        districts=(),
        monthly_rent_yuan=None,
        area_sqm=None,
        rooms=(),
        orientations=(),
        tags=(),
        page=1,
        page_size=1,
    )
    request = _build_search_request(filters)
    return AuthorizedRequest(
        route="rental_listing.get_detail",
        method=request.method,
        query=request.query,
        body=request.body,
        request_id=str(uuid.uuid4()),
    )


def _build_whoami_request() -> AuthorizedRequest:
    return AuthorizedRequest(
        route="identity.me",
        method="GET",
        query={"typeList": "2"},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _coerce_mapping(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise UpstreamChangedError("upstream response body is not a JSON object")
    return body


def _raise_for_business_code(body: Mapping[str, Any]) -> None:
    code = body.get("code")
    if code == _CODE_OK or code == 0:
        return
    if code == _CODE_INVALID_INPUT:
        raise UpstreamInvalidInputError(str(body.get("msg") or "upstream rejected input"))
    if code == _CODE_UNAUTHORIZED:
        # Let the session provider's auth-failure detection handle this; we
        # surface a contract-changed error only if the auth boundary did not
        # already raise AuthenticationRequiredError first.
        raise UpstreamChangedError("upstream reported 403 but auth boundary did not raise")
    raise UpstreamChangedError(
        f"upstream returned unknown business code={code!r} msg={body.get('msg')!r}"
    )


def _parse_listing(row: Mapping[str, Any], scope: str) -> RentalListing:
    # Fields are documented in docs/crm-auth-flow-analysis.md §4. Missing
    # fields become None rather than KeyError to keep contract drift observable
    # in logs rather than fatal to the whole page.
    orientation_list = row.get("orientation") or []
    if isinstance(orientation_list, (list, tuple)) and orientation_list:
        orientation = ",".join(str(o) for o in orientation_list)
    else:
        orientation = None
    bedroom = row.get("bedroomAmount")
    hall = row.get("hallAmount")
    bathroom = row.get("bathroomAmount")
    layout_parts = []
    if isinstance(bedroom, (int, float)):
        layout_parts.append(f"{int(bedroom)}室")
    if isinstance(hall, (int, float)):
        layout_parts.append(f"{int(hall)}厅")
    if isinstance(bathroom, (int, float)):
        layout_parts.append(f"{int(bathroom)}卫")
    layout = "".join(layout_parts) or None
    return RentalListing(
        listing_id=str(row.get("delCode") or ""),
        community=str(row.get("resblockName") or ""),
        layout=layout,
        area_sqm=_as_float(row.get("area")),
        monthly_rent_yuan=_as_float(row.get("price")),
        orientation=orientation,
        visible_scope=scope,
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_page(
    body: Mapping[str, Any], filters: RentalListingFilters, request_id: str
) -> RentalListingPage:
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise UpstreamChangedError("upstream response missing 'data' object")
    raw_list = data.get("result")
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("upstream response 'data.result' is not an array")
    items = tuple(_parse_listing(row, filters.scope) for row in raw_list)
    total_count = _as_int(data.get("totalCount")) or 0
    page = max(filters.page, 1)
    page_size = max(filters.page_size, 1)
    has_more = page * page_size < total_count
    return RentalListingPage(
        items=items,
        page=page,
        page_size=page_size,
        has_more=has_more,
        request_id=request_id,
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _parse_principal(body: Mapping[str, Any]) -> Principal:
    # accountRightInfo does not return a stable employee principal field in
    # the documented envelope; the principal is read from the auth material
    # by the bootstrap provider. Here we accept either a top-level ``ucid``
    # (some profiles) or raise UpstreamChangedError so drift is visible.
    data = body.get("data")
    if isinstance(data, Mapping) and data.get("ucid"):
        return Principal(
            employee_principal=str(data["ucid"]),
            display_name=_opt_str(data.get("name")),
        )
    # Fallback: headerData-style envelope. Caller-spec'd in docs §6.
    if isinstance(data, Mapping) and data.get("user"):
        user = data["user"]
        if isinstance(user, Mapping):
            return Principal(
                employee_principal=str(user.get("ucid") or ""),
                display_name=_opt_str(user.get("name")),
            )
    raise UpstreamChangedError("accountRightInfo response has no recognisable principal")


def _opt_str(value: Any) -> str | None:
    return str(value) if value is not None else None


class KecomCrmClient:
    """CRM business adapter that drives upstream calls via SessionProvider.

    Implements ``app.domain.providers.crm_client.CrmClient``. It never touches
    authentication material directly: every upstream request goes through
    ``SessionProvider.authorized_fetch`` so cookies stay inside the auth
    boundary. Responses are parsed against the contract documented in
    ``docs/crm-auth-flow-analysis.md`` §4 and mapped to domain models; any
    unrecognised envelope raises ``UpstreamChangedError`` so the connector
    degrades visibly rather than returning partial data.
    """

    def __init__(self, session_provider: SessionProvider) -> None:
        self._session = session_provider

    # -- CrmClient protocol ------------------------------------------------

    def whoami(self) -> Principal:
        response = self._session.authorized_fetch(_build_whoami_request())
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"identity.me returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        return _parse_principal(body)

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        request = _build_search_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.search returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        return _parse_page(body, filters, request.request_id)

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        request = _build_detail_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.get_detail returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        page = _parse_page(
            body,
            RentalListingFilters(
                community_keyword=None,
                listing_id=listing_id,
                maintainer=None,
                scope="my_maintained",
                districts=(),
                monthly_rent_yuan=None,
                area_sqm=None,
                rooms=(),
                orientations=(),
                tags=(),
                page=1,
                page_size=1,
            ),
            request.request_id,
        )
        if not page.items:
            raise UpstreamChangedError(f"upstream returned no listing for id={listing_id!r}")
        return page.items[0]


# satisfy static Protocol check without runtime isinstance
_CrmClientProtocol = CrmClientProtocol
