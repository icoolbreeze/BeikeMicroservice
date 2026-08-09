from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from app.domain.errors import (
    AuthenticationRequiredError,
    UpstreamChangedError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    MapBounds,
    Principal,
    RentalListingFilterOption,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
    RentalMapBubble,
    RentalMapBubbleFilters,
    RentalMapListing,
    RentalMapPage,
    RentalMapSearchFilters,
    RentalMapSuggestion,
    RentalMapSuggestionFilters,
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

# scope -> upstream relationRange (from the live 范围 catalog: 1 维护盘,
# 4 店共享池, 9 角色房源).  The old code hard-coded 1 and never read scope.
_SCOPE_TO_RELATION_RANGE = {
    "my_maintained": 1,
    "shared": 4,
    "role_visible": 9,
}


def _route_query(filters: RentalListingFilters) -> dict[str, str | int]:
    """Map RentalListingFilters to the upstream search query string.

    Only documented params are emitted; unknown filters are dropped rather
    than smuggled to the upstream. ``community_keyword`` becomes the
    upstream ``communityKeyword`` (case-sensitive on the CRM side).
    """
    params: dict[str, str | int] = {
        "pageIndex": filters.page,
        "pageSize": filters.page_size,
        "relationRange": _SCOPE_TO_RELATION_RANGE.get(filters.scope, 1),
        "sceneCode": _SEARCH_SCENE_CODE,
        "clientOsType": _SEARCH_CLIENT_OS,
    }
    if filters.community_keyword:
        params["communityKeyword"] = filters.community_keyword
    if filters.resblock_ids:
        # Captured from the 房源列表 page's selectedCommunityIdList: the UI
        # submits normal community suggestions as a comma-separated
        # ``resblockId`` query parameter.
        params["resblockId"] = ",".join(filters.resblock_ids)
    if filters.listing_id:
        params["delCode"] = filters.listing_id
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
    params.update(dict(filters.condition_filters))
    return params


def _build_search_request(filters: RentalListingFilters) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="rental_listing.search",
        method="GET",
        query=_route_query(filters),
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_filter_options_request() -> AuthorizedRequest:
    return AuthorizedRequest(
        route="rental_listing.filter_options",
        method="GET",
        query={},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_detail_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §8.4): the page calls
    # /api/puzu/house/detail/detailHead?delCode=<id> for the house record.
    return AuthorizedRequest(
        route="rental_listing.get_detail",
        method="GET",
        query={"delCode": listing_id},
        body=None,
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


def _bounds_query(bounds: MapBounds) -> dict[str, float]:
    """Use the map SDK's camel-case viewport contract.

    The browser's ``bubblelist`` and ``houselist`` requests use these exact
    keys. Sending the domain model's snake-case names makes the map gateway
    reject otherwise valid community queries with ``errno=10002``.
    """
    return {
        "minLongitude": bounds.min_longitude,
        "maxLongitude": bounds.max_longitude,
        "minLatitude": bounds.min_latitude,
        "maxLatitude": bounds.max_latitude,
    }


def _map_condition(filters: tuple[str, ...]) -> str:
    return "".join(filters)


def _build_map_search_request(filters: RentalMapSearchFilters) -> AuthorizedRequest:
    query: dict[str, str | int | float] = {
        "cityId": filters.city_id,
        "dataSource": filters.data_source,
        "curPage": filters.page,
        "condition": _map_condition(filters.condition_tokens),
    }
    if filters.mode == "circle":
        query["resblockIds"] = ",".join(filters.resblock_ids)
        route = "rental_map.search_circle"
    else:
        query.update(_bounds_query(filters.bounds))
        query["type"] = filters.result_type or "1"
        query["resblockId"] = filters.resblock_id or ""
        route = "rental_map.search"
    return AuthorizedRequest(
        route=route,
        method="GET",
        query=query,
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_map_bubbles_request(filters: RentalMapBubbleFilters) -> AuthorizedRequest:
    query: dict[str, str | int | float] = {
        "cityId": filters.city_id,
        "dataSource": filters.data_source,
        "condition": _map_condition(filters.condition_tokens),
        "id": filters.group_id or "",
        "groupType": filters.group_type,
    }
    query.update(_bounds_query(filters.bounds))
    return AuthorizedRequest(
        route="rental_map.bubbles",
        method="GET",
        query=query,
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_map_suggest_request(filters: RentalMapSuggestionFilters) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="rental_map.suggest",
        method="GET",
        query={
            "cityId": filters.city_id,
            "dataSource": filters.data_source,
            "query": filters.query,
            "pageSize": 30,
            "hlsPreTag": "<i>",
            "hlsPostTag": "</i>",
        },
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _coerce_mapping(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        raise UpstreamChangedError("upstream response body is not a JSON object")
    return body


def _coerce_map_body(body: Any) -> Mapping[str, Any]:
    if isinstance(body, Mapping):
        return body
    if isinstance(body, list):
        return {"list": body}
    raise UpstreamChangedError("map upstream response body is not a JSON object")


def _map_payload(body: Any) -> Mapping[str, Any]:
    envelope = _coerce_map_body(body)
    code = envelope.get("code")
    msg = envelope.get("msg")
    if code in (403, "403", 31002, "31002") or (
        isinstance(msg, str) and ("未登录" in msg or "请先登录" in msg)
    ):
        raise AuthenticationRequiredError("CRM map authorization was rejected")
    if code in (100001, "100001"):
        raise UpstreamInvalidInputError(str(msg or "map upstream rejected input"))
    if code not in (None, 0, "0", 100000, "100000"):
        raise UpstreamChangedError(
            f"map upstream returned unknown code={code!r} msg={msg!r}"
        )
    errno = envelope.get("errno")
    if errno not in (None, 0, "0"):
        raise UpstreamChangedError(f"map upstream returned errno={errno!r}")
    data = envelope.get("data")
    if isinstance(data, Mapping) and not any(key in envelope for key in ("list", "bubbleList")):
        return data
    return envelope


def _map_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _map_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _map_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _extract_id_from_action_url(value: Any) -> str | None:
    """Pull the house id from the map card's ``actionUrl`` link.

    The map upstream carries the listing identifier only inside the card
    action URL (``https://trusteeship.link.lianjia.com/house/detail/<id>``);
    the row has no ``delCode``/``houseId``/``listingId``/``id`` field.
    """
    text = _map_text(value)
    if not text:
        return None
    return text.rstrip("/").split("/")[-1] or None


def _parse_map_listing(row: Mapping[str, Any]) -> RentalMapListing:
    raw_tags = row.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, Mapping):
                text = _map_text(tag.get("desc") or tag.get("name"))
            else:
                text = _map_text(tag)
            if text:
                tags.append(text)
    return RentalMapListing(
        listing_id=_map_text(
            row.get("delCode")
            or row.get("houseId")
            or row.get("listingId")
            or row.get("id")
            or _extract_id_from_action_url(row.get("actionUrl"))
        )
        or "",
        title=_map_text(row.get("title") or row.get("houseTitle")) or "",
        description=_map_text(row.get("desc") or row.get("description")) or "",
        tags=tuple(tags),
        price_text=_map_text(row.get("priceStr") or row.get("price")),
        unit_price_text=_map_text(row.get("unitPriceStr") or row.get("unitPrice")),
    )


def _parse_map_page(
    body: Any, filters: RentalMapSearchFilters, request_id: str
) -> RentalMapPage:
    payload = _map_payload(body)
    raw_list = payload.get("list") or payload.get("result") or []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("map upstream response 'list' is not an array")
    items = tuple(
        _parse_map_listing(row)
        for row in raw_list
        if isinstance(row, Mapping)
    )
    page = _map_int(payload.get("page")) or filters.page
    total = _map_int(payload.get("total") or payload.get("totalCount")) or len(items)
    has_more_value = payload.get("hasMore")
    if isinstance(has_more_value, bool):
        has_more = has_more_value
    else:
        has_more = bool(items) and page * len(items) < total
    return RentalMapPage(
        items=items,
        page=page,
        total=total,
        has_more=has_more,
        mode=filters.mode,
        request_id=request_id,
    )


def _parse_map_bubbles(
    body: Any, group_type: str
) -> tuple[RentalMapBubble, ...]:
    payload = _map_payload(body)
    raw_list = payload.get("bubbleList") or payload.get("list") or []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("map upstream response 'bubbleList' is not an array")
    return tuple(
        RentalMapBubble(
            bubble_id=_map_text(row.get("id")) or "",
            name=_map_text(row.get("name")) or "",
            group_type=group_type,
            latitude=_map_float(row.get("latitude")),
            longitude=_map_float(row.get("longitude")),
            count=_map_int(row.get("count")),
            count_text=_map_text(row.get("countStr")),
            price_text=_map_text(row.get("priceStr")),
        )
        for row in raw_list
        if isinstance(row, Mapping)
    )


def _parse_map_suggestions(body: Any) -> tuple[RentalMapSuggestion, ...]:
    payload = _map_payload(body)
    raw_list = payload.get("list") or []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("map upstream response 'list' is not an array")
    return tuple(
        RentalMapSuggestion(
            item_type=_map_text(row.get("itemType")) or "",
            item_type_name=_map_text(row.get("itemTypeName")),
            item_id=_map_text(row.get("itemId")) or "",
            name=_map_text(row.get("itemName")) or "",
            count_text=_map_text(row.get("countStr")),
            latitude=_map_float(row.get("pointLat")),
            longitude=_map_float(row.get("pointLng")),
        )
        for row in raw_list
        if isinstance(row, Mapping)
    )


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
        # 2 = 普租, 5 = 托管. Only 普租 ids resolve via detailHead.
        del_type=_as_int(row.get("delType")),
    )


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_detail_head(body: Mapping[str, Any]) -> RentalListing:
    # Captured from the live detail page (docs §8.4): detailHead returns the
    # house record under ``data`` with its own field names (housePrice,
    # houseArea, livingroomAmount, oriented). An empty ``data`` object is the
    # upstream's explicit "no such listing" answer — never fall back to a
    # search-returned listing for a different house (the pre-detailHead
    # behaviour that returned ICC凯旋门 for a trusteeship-domain id).
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        # Empty data is the upstream's "no such listing" answer. In practice
        # this covers trusteeship (托管, delType=5) ids: detailHead only serves
        # the 普租 domain, so callers should not attempt detail on 托管 ids.
        raise UpstreamInvalidInputError(
            "no detailHead record for this id; trusteeship (托管) listings are "
            "not covered by the 普租 detail endpoint"
        )
    del_code = _as_int(data.get("delCode"))
    if del_code is None:
        raise UpstreamChangedError("detailHead response 'data' has no delCode")
    bedroom = _as_int(data.get("bedroomAmount"))
    hall = _as_int(data.get("livingroomAmount"))
    bathroom = _as_int(data.get("bathroomAmount"))
    layout_parts = []
    if bedroom is not None:
        layout_parts.append(f"{bedroom}室")
    if hall is not None:
        layout_parts.append(f"{hall}厅")
    if bathroom is not None:
        layout_parts.append(f"{bathroom}卫")
    return RentalListing(
        listing_id=str(del_code),
        community=_opt_str(data.get("resblockName")) or "",
        layout="".join(layout_parts) or None,
        area_sqm=_as_float(data.get("houseArea")),
        monthly_rent_yuan=_as_float(data.get("housePrice")),
        orientation=_parse_orientation(data.get("oriented")),
        # detailHead has no list scope; "detail" marks the direct-detail view.
        visible_scope="detail",
    )


def _parse_orientation(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and value:
        return ",".join(str(item) for item in value)
    if isinstance(value, Mapping):
        directs = value.get("directs")
        if isinstance(directs, (list, tuple)) and directs:
            return ",".join(str(item) for item in directs)
    if isinstance(value, str) and value:
        return value
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


def _parse_filter_option(row: Mapping[str, Any]) -> RentalListingFilterOption:
    raw_children = row.get("children") or []
    if not isinstance(raw_children, list):
        raise UpstreamChangedError("listing filter option children is not an array")
    return RentalListingFilterOption(
        key=_opt_str(row.get("key")),
        name=_opt_str(row.get("name")) or "",
        value=_filter_scalar(row.get("value")),
        selection_type=_opt_str(row.get("type")) or "",
        default_value=_filter_scalar(row.get("defaultValue")),
        children=tuple(
            _parse_filter_option(child)
            for child in raw_children
            if isinstance(child, Mapping)
        ),
    )


def _parse_filter_options(body: Mapping[str, Any]) -> tuple[RentalListingFilterOption, ...]:
    raw_options = body.get("data")
    if not isinstance(raw_options, list):
        raise UpstreamChangedError("listing filter-options response 'data' is not an array")
    return tuple(
        _parse_filter_option(option)
        for option in raw_options
        if isinstance(option, Mapping)
    )


def _filter_scalar(value: Any) -> str | int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return value
    return None


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
        # The upstream accountRightInfo envelope does not echo the employee
        # principal (verified 2026-08-07), so the identity recorded at scan
        # time is the authoritative local source. The upstream path below is
        # kept for profiles that do expose a ucid.
        principal = self._session.bound_principal()
        if principal is not None:
            return principal

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

    def rental_listing_filter_options(self) -> tuple[RentalListingFilterOption, ...]:
        request = _build_filter_options_request()
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.filter_options returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        return _parse_filter_options(body)

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        # Direct detailHead call (docs §8.4) instead of a search round-trip:
        # search with listing_id may return a different house when the id is
        # from the trusteeship domain, so an unknown id must fail loudly here.
        request = _build_detail_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.get_detail returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        return _parse_detail_head(body)

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        request = _build_map_search_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"{request.route} returned status {response.status_code}"
            )
        return _parse_map_page(response.body, filters, request.request_id)

    def rental_map_bubbles(
        self, filters: RentalMapBubbleFilters
    ) -> tuple[RentalMapBubble, ...]:
        request = _build_map_bubbles_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_map.bubbles returned status {response.status_code}"
            )
        return _parse_map_bubbles(response.body, filters.group_type)

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        request = _build_map_suggest_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_map.suggest returned status {response.status_code}"
            )
        return _parse_map_suggestions(response.body)


# satisfy static Protocol check without runtime isinstance
_CrmClientProtocol = CrmClientProtocol
