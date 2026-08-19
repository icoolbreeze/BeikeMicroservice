from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

from app.domain.errors import (
    AuthenticationRequiredError,
    UpstreamChangedError,
    UpstreamInvalidInputError,
)
from app.domain.models import (
    FollowRecord,
    HqiHeatItem,
    HqiScore,
    HqiSuggestion,
    ListingDetailInfo,
    ListingMaintainInfo,
    ListingPropertyInfo,
    ListingProspect,
    MaintainField,
    MaintainModule,
    MapBounds,
    Principal,
    ProspectPhoto,
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
    SaleCommunitySuggestion,
    SaleFollowRecord,
    SaleListing,
    SaleListingDetail,
    SaleListingFilters,
    SaleListingFilterOption,
    SaleListingPage,
    SaleMaintainField,
    SaleMaintainInfo,
    SaleMaintainModule,
    SaleMapBubble,
    SaleMapBubbleFilters,
    SaleMapSuggestion,
    TrusteeshipDeal,
    TrusteeshipDealPage,
    TrusteeshipDetail,
    TrusteeshipListingPage,
    TrusteeshipListingRow,
    TrusteeshipManagerInfo,
    TrusteeshipProspectPhoto,
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

# scope -> upstream relationRange (from the live 范围 catalog: 0 不限,
# 1 维护盘, 4 店共享池, 9 角色房源).
_SCOPE_TO_RELATION_RANGE = {
    "all": 0,
    "my_maintained": 1,
    "shared": 4,
    "role_visible": 9,
}


def _route_query(filters: RentalListingFilters) -> dict[str, str | int]:
    """Map RentalListingFilters to the upstream search query string.

    Only documented params are emitted. ``condition_filters`` keys are
    allow-listed upstream of here (``_LISTING_CONDITION_KEYS`` in the API
    schemas) and are forwarded verbatim once accepted — this function does
    not re-filter them. ``community_keyword`` becomes the upstream
    ``communityKeyword`` (case-sensitive on the CRM side).
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


def _build_redirect_request(listing_id: str) -> AuthorizedRequest:
    # The 房源列表 page resolves a managed (del_type=5) row's destination by
    # calling /api/houseList/search/getRedirectUrl?delCode=<id>&delType=5; the
    # response ``data`` is the trusteeship detail URL whose last segment is
    # the ``cell_code``. Mirrors the employee's real click path (verified via
    # injected-credential Playwright capture).
    return AuthorizedRequest(
        route="rental_listing.get_redirect_url",
        method="GET",
        query={"delCode": listing_id, "delType": "5"},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_detail_prospect_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详情): the page calls
    # /api/puzu/house/detail/detailProspect?delCode=<id> for the 实勘 record.
    return AuthorizedRequest(
        route="rental_listing.detail_prospect",
        method="GET",
        query={"delCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_hdic_info_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详细信息): the page calls
    # /api/puzu/house/detail/detailHdicInfo?delCode=<id> for the
    # 小区/楼栋 property attributes.
    return AuthorizedRequest(
        route="rental_listing.get_hdic_info",
        method="GET",
        query={"delCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_house_label_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详细信息): the page calls
    # /api/puzu/house/detail/getHouseLabel?delCode=<id> for the house labels.
    return AuthorizedRequest(
        route="rental_listing.get_house_label",
        method="GET",
        query={"delCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_maintain_info_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详细信息): the page calls
    # /api/puzuHouse/puzu/house/detail/app/getMaintainInfo?delCode=<id> for
    # the 维护信息 section (家具/家电/租期/装修/入住时间/备注…). Probed
    # 2026-08-09: plain delCode suffices, no isApp / city header needed.
    return AuthorizedRequest(
        route="rental_listing.get_maintain_info",
        method="GET",
        query={"delCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_follow_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详细信息): the page calls
    # /api/puzu/house/detail/detailFollow?delCode=<id> for the 跟进记录 list.
    # Probed 2026-08-09: plain delCode suffices; missing id returns
    # code=100001 房源编码错误 (same invalid-input semantics as detailHead).
    # pageSize=100 so the 跟进 history (latest status, key situation, 可否看房)
    # is returned in full: a 50-house sample (2026-08-09) hit 82 records on one
    # listing, so the default page of 8 and even 50 truncate real histories.
    return AuthorizedRequest(
        route="rental_listing.get_follow",
        method="GET",
        query={"delCode": listing_id, "pageSize": "100"},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_trusteeship_detail_request(cell_code: str) -> AuthorizedRequest:
    # Captured live 2026-08-15 from the 托管 (省心租) detail page: the SPA
    # calls /api/trusteeship/broker/out/detail/pageInfoForPc?cellCode=<id>
    # for the head record including the 实勘 photo list.
    return AuthorizedRequest(
        route="trusteeship.get_detail",
        method="GET",
        query={"cellCode": cell_code},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_trusteeship_deals_request(
    cell_code: str, *, page: int, page_size: int
) -> AuthorizedRequest:
    # Captured live 2026-08-15 from the 托管 detail page's 成交参考 block:
    # /api/vRoute/house/trusteeship/broker/out/deal/list?cellCode=<id>
    # &pageIndex=1&pageSize=5.
    return AuthorizedRequest(
        route="trusteeship.get_deals",
        method="GET",
        query={"cellCode": cell_code, "pageIndex": page, "pageSize": page_size},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_trusteeship_list_request(
    *, page: int, page_size: int, cell_code: str | None
) -> AuthorizedRequest:
    # Captured live 2026-08-15 from the 托管工作台 待出租 tab:
    # POST /api/house/search/waitingrent {"pageIndex":1,"pageSize":20,
    # "sort":"waitRentTime:desc","searchStatus":1}. The 房源编码 box adds
    # delCode+outHouseCode carrying a trusteeship cell code (bizCode) —
    # verified to return exactly that unit. pageSize is server-capped at 300.
    body: dict[str, str | int] = {
        "pageIndex": page,
        "pageSize": page_size,
        "sort": "waitRentTime:desc",
        "searchStatus": 1,
    }
    if cell_code:
        body["delCode"] = cell_code
        body["outHouseCode"] = cell_code
    return AuthorizedRequest(
        route="trusteeship.search_listings",
        method="POST",
        query=None,
        body=body,
        request_id=str(uuid.uuid4()),
    )


def _build_hqi_tab_request(listing_id: str) -> AuthorizedRequest:
    # Captured from the live detail page (docs §房源详细信息): the page calls
    # /api/puzu/house/detail/detailHqiTab?isApp=false&delCode=<id> for the
    # HQI score. isApp=false is REQUIRED — without it the upstream rejects
    # the call with 缺少必要的入参 (probed 2026-08-09).
    return AuthorizedRequest(
        route="rental_listing.get_hqi_tab",
        method="GET",
        query={"delCode": listing_id, "isApp": "false"},
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


# -- 买卖 (sale) request construction ----------------------------------------
# Captured live 2026-08-11 from house.link.lianjia.com/search/sale/default/gdiv_mt
# (docs/sale-api-catalog.md). Fixed params mirror the browser's searchQueryNew
# call; filter values are the catalog ids (getSearchFilters).

_SALE_FIXED_QUERY: dict[str, str | int] = {
    "alertContent": "",
    "alertTitle": "",
    "algorithmPunishType": 0,
    "buttonVoList": "",
    "del_type": 1,
    "evtId": "",
    "level": 0,
    "maskAllHouse": "false",
    "punish": "false",
    "punishCode": "500100000004",
    "riskLabelAction": 0,
    "riskLabelPerson": 1,
    "riskProtectMainHouse": 0,
    "riskStrategy": "",
    "riskStrategyInfo": "",
    "season": "",
    "tabSort": "default",
    "timeLocal": "",
    "ucid": "",
}

# Sort tokens captured from the live table header controls.
_SALE_SORT_TOKENS = frozenset({
    "period1_desc_createtime_desc",  # 默认：新上房源优先
    "period1_asc_totalprice",
    "period1_desc_totalprice",
})


def _range_bound(value: float | None, *, open_high: bool = False) -> int:
    """Round a price/area range bound to an integer upstream token.

    Price/area are whole 万元/平米 on the CRM side; float bounds from the
    MCP NumericRange are normalised here so the emitted token matches the
    page's catalog ids (50,70 not 50.0,70.0)."""
    if value is None:
        return -1 if open_high else 0
    return int(round(value))


def _sale_query(filters: SaleListingFilters) -> dict[str, str | int]:
    params: dict[str, str | int] = dict(_SALE_FIXED_QUERY)
    params["currentPage"] = filters.page
    params["vertical"] = filters.scope
    params["sort"] = filters.sort
    if filters.listing_id:
        params["del_code"] = filters.listing_id
    if filters.community_ids:
        params["multi_community_id"] = ",".join(filters.community_ids)
    if filters.district_id:
        params["disId"] = filters.district_id
    if filters.price_wan is not None:
        lo, hi = filters.price_wan
        params["price"] = f"{_range_bound(lo)},{_range_bound(hi, open_high=True)}"
    if filters.area_sqm is not None:
        lo, hi = filters.area_sqm
        params["area"] = f"{_range_bound(lo)},{_range_bound(hi, open_high=True)}"
    if filters.rooms:
        rooms = sorted(filters.rooms)
        lo, hi = rooms[0], rooms[-1]
        params["room"] = f"{lo},{hi}"
    if filters.floors:
        params["floorNew"] = ",".join(filters.floors)
    if filters.orientations:
        params["orient"] = ",".join(filters.orientations)
    if filters.house_layouts:
        params["houseLayout"] = ",".join(filters.house_layouts)
    if filters.tags:
        params["tag"] = ",".join(filters.tags)
    if filters.house_age is not None:
        params["h_age"] = filters.house_age
    if filters.visitable_times is not None:
        params["visitable_times"] = filters.visitable_times
    if filters.payment_mode:
        params["payment_mode"] = filters.payment_mode
    if filters.building_type:
        params["b_type"] = filters.building_type
    for key, value in filters.select:
        if value and value != "-1":
            params[key] = value
    return params


def _build_sale_search_request(filters: SaleListingFilters) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.search",
        method="GET",
        query=_sale_query(filters),
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_filter_options_request() -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.filter_options",
        method="GET",
        query={"del_type": 1, "searchTab": "ALL_TAB"},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_suggest_request(query: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.suggest",
        method="GET",
        query={"delType": 1, "q": query},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_detail_request(listing_id: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.get_detail",
        method="GET",
        query={"housedelCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_ext_info_request(listing_id: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.get_ext_info",
        method="GET",
        query={"housedelCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_maintain_info_request(listing_id: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.get_maintain_info",
        method="GET",
        query={"housedelCode": listing_id},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_follow_request(listing_id: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_listing.get_follow",
        method="GET",
        query={"type": 0, "currentPage": 1, "housedelCode": listing_id, "pageSize": 100},
        body=None,
        request_id=str(uuid.uuid4()),
    )


# -- 买卖 地图找房 (sale map) request construction --------------------------
# Captured live 2026-08-11 from house.link.lianjia.com/search/sale/mapSearch.
# The sale map domain lives on house.link itself: /search/map/suggest and
# /search/map/bubbleSearch (unlike the rental map which proxies map.ke.com).

_SALE_MAP_FIXED_QUERY = {
    "deltype": 1,
    "ucid": None,
    "evtId": None,
    "punishCode": "500100000004",
    "alertContent": None,
    "alertTitle": None,
    "buttonVoList": None,
    "season": None,
    "riskStrategy": None,
    "riskStrategyInfo": None,
    "timeLocal": None,
    "algorithmPunishType": 0,
    "level": 0,
    "riskLabelPerson": 1,
    "riskLabelAction": 0,
    "riskProtectMainHouse": 0,
    "maskAllHouse": "false",
    "punish": "false",
}


def _build_sale_map_suggest_request(query: str, city_id: str) -> AuthorizedRequest:
    return AuthorizedRequest(
        route="sale_map.suggest",
        method="GET",
        query={"deltype": 1, "city_id": city_id, "query": query},
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _build_sale_map_bubbles_request(filters: SaleMapBubbleFilters) -> AuthorizedRequest:
    query: dict[str, str | int | float | None] = dict(_SALE_MAP_FIXED_QUERY)
    query.update(
        {
            "city_id": filters.city_id,
            "group_type": filters.group_type,
            "max_lat": filters.bounds.max_latitude,
            "min_lat": filters.bounds.min_latitude,
            "max_lng": filters.bounds.max_longitude,
            "min_lng": filters.bounds.min_longitude,
            "filters": _sale_map_filters_blob(filters.filters),
        }
    )
    return AuthorizedRequest(
        route="sale_map.bubbles",
        method="GET",
        query=query,
        body=None,
        request_id=str(uuid.uuid4()),
    )


def _sale_map_filters_blob(filters: dict[str, object]) -> str:
    """The map page sends filters as a JSON string (empty object when none)."""
    import json

    return json.dumps(filters, ensure_ascii=False, separators=(",", ":"))


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


# 托管 (省心租) photo URLs are returned as root-relative paths
# (``/lease-image/house/...``) by pageInfoForPc; the SPA prefixes this CDN
# origin before appending an image instruction suffix. We keep the raw URL
# stable (no suffix) and expose the absolute form only.
_CDN_IMAGE_BASE = "https://img.ljcdn.com"


def _trusteeship_envelope(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a trusteeship business envelope and return its ``data``."""
    _raise_for_business_code(body)
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise UpstreamChangedError("trusteeship response 'data' is not an object")
    return data


def _trusteeship_image_url(value: Any) -> str | None:
    raw = _opt_str(value)
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"{_CDN_IMAGE_BASE}{raw}"


def _parse_trusteeship_deal(row: Mapping[str, Any]) -> TrusteeshipDeal:
    return TrusteeshipDeal(
        deal_price=_opt_str(row.get("dealPrice")),
        deal_time=_opt_str(row.get("dealTime")),
        desc=_opt_str(row.get("desc")),
        layout_url=_trusteeship_image_url(row.get("layout")),
        prospect_url=_trusteeship_image_url(row.get("prospect")),
        on_rent_time=_opt_str(row.get("onRentTime")),
    )


def _parse_trusteeship_deals(
    body: Mapping[str, Any], *, page: int, request_id: str
) -> TrusteeshipDealPage:
    data = _trusteeship_envelope(body)
    raw_list = data.get("result") or []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("trusteeship deal/list 'result' is not an array")
    items = tuple(
        _parse_trusteeship_deal(row) for row in raw_list if isinstance(row, Mapping)
    )
    total = _as_int(data.get("totalCount")) or len(items)
    has_more = bool(data.get("hasMore"))
    return TrusteeshipDealPage(
        items=items,
        page=page,
        total=total,
        has_more=has_more,
        request_id=request_id,
    )


def _parse_trusteeship_listing_row(row: Mapping[str, Any]) -> TrusteeshipListingRow:
    bedroom = _as_int(row.get("bedroomAmount"))
    bathroom = _as_int(row.get("bathroomAmount"))
    layout_text = None
    if bedroom is not None or bathroom is not None:
        layout_text = f"{bedroom or 0}室{bathroom or 0}卫"
    return TrusteeshipListingRow(
        cell_code=str(row.get("bizCode") or ""),
        community=_opt_str(row.get("resblockName")),
        biz_circle=_opt_str(row.get("bizCircleName")),
        building_name=_opt_str(row.get("buildingName")),
        house_name=_opt_str(row.get("houseName")),
        layout_text=layout_text,
        area_sqm=_as_float(row.get("cellArea")),
        floor=_as_int(row.get("floorNum")),
        guide_price_yuan=_as_int(row.get("guidePrice")),
        can_look_time=_opt_str(row.get("canLookTime")) or _opt_str(row.get("canLookTimDesc")),
    )


def _parse_trusteeship_listings(
    body: Mapping[str, Any], *, page: int, page_size: int, request_id: str
) -> TrusteeshipListingPage:
    data = _trusteeship_envelope(body)
    raw_list = data.get("list") or []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("trusteeship waitingrent 'list' is not an array")
    items = tuple(
        _parse_trusteeship_listing_row(row)
        for row in raw_list
        if isinstance(row, Mapping) and row.get("bizCode")
    )
    total = _as_int(data.get("total")) or len(items)
    has_more = bool(data.get("more")) or page * page_size < total
    return TrusteeshipListingPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        has_more=has_more,
        request_id=request_id,
    )


def _parse_trusteeship_fee_groups(fee_info: Any) -> tuple[str, ...]:
    """Flatten feeInfoDto.feeConfigDataList into render-ready lines.

    Each group is a matrix row of ``{desc, descV2, type}`` cells; joining the
    cell descs with ' | ' preserves the 类型/租期/出房类型/支付周期/月租金/
    服务费/押金 column order the page displays.
    """
    if not isinstance(fee_info, Mapping):
        return ()
    raw_groups = fee_info.get("feeConfigDataList")
    if not isinstance(raw_groups, list):
        return ()
    lines: list[str] = []
    for group in raw_groups:
        if not isinstance(group, list):
            continue
        cells = [
            str(cell.get("descV2") or cell.get("desc") or "").strip()
            for cell in group
            if isinstance(cell, Mapping)
        ]
        line = " | ".join(cells).rstrip(";| ")
        if line:
            lines.append(line)
    return tuple(lines)


def _parse_trusteeship_detail(
    body: Mapping[str, Any], cell_code: str
) -> TrusteeshipDetail:
    data = _trusteeship_envelope(body)
    head = data.get("houseHeadInfo")
    if not isinstance(head, Mapping):
        raise UpstreamChangedError("pageInfoForPc has no houseHeadInfo object")

    manager = head.get("managerInfo")
    manager_info = (
        TrusteeshipManagerInfo(
            user_name=_opt_str(manager.get("userName")),
            role_name=_opt_str(manager.get("roleName")),
            org_name=_opt_str(manager.get("orgName")),
            phone=_opt_str(manager.get("phone")),
        )
        if isinstance(manager, Mapping)
        else None
    )

    key_info = head.get("keyInfo")
    key_desc = None
    has_smart_key = None
    if isinstance(key_info, Mapping):
        key_desc = _opt_str(key_info.get("desc"))
        has_smart_key = _as_bool(key_info.get("hasSmartKey"))

    out_show = head.get("outShow")
    out_show_desc = (
        _opt_str(out_show.get("showDesc")) if isinstance(out_show, Mapping) else None
    )

    vr = head.get("vrDataDetail")
    vr_url = None
    vr_picture_url = None
    if isinstance(vr, Mapping):
        vr_url = _opt_str(vr.get("vrUrl"))
        vr_picture_url = _opt_str(vr.get("pictureUrl"))

    hqi = head.get("hqiEntranceDto")
    hqi_score = _opt_str(hqi.get("hqiScore")) if isinstance(hqi, Mapping) else None

    tags: list[str] = []
    raw_tags = head.get("tagList")
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, Mapping):
                desc = _opt_str(tag.get("desc"))
                if desc:
                    tags.append(desc)

    prospects: list[TrusteeshipProspectPhoto] = []
    raw_prospects = head.get("houseProspectList")
    if isinstance(raw_prospects, list):
        for item in raw_prospects:
            if not isinstance(item, Mapping):
                continue
            url = _trusteeship_image_url(item.get("url"))
            if not url:
                continue
            prospects.append(
                TrusteeshipProspectPhoto(
                    name=_opt_str(item.get("name")),
                    url=url,
                    primary_flag=bool(item.get("primaryFlag")),
                    create_time=_opt_str(item.get("createTime")),
                )
            )

    house_type_images: list[str] = []
    raw_type_list = head.get("houseTypeList")
    if isinstance(raw_type_list, list):
        for item in raw_type_list:
            if isinstance(item, Mapping):
                url = _trusteeship_image_url(item.get("url"))
                if url:
                    house_type_images.append(url)

    deal_info = data.get("dealInfo")
    deal_details: tuple[TrusteeshipDeal, ...] = ()
    deal_avg_price = None
    deal_total_count = None
    if isinstance(deal_info, Mapping):
        raw_details = deal_info.get("dealDetails")
        if isinstance(raw_details, list):
            deal_details = tuple(
                _parse_trusteeship_deal(row)
                for row in raw_details
                if isinstance(row, Mapping)
            )
        inner = deal_info.get("dealInfo")
        if isinstance(inner, Mapping):
            # upstream serves both as numeric strings ("2416"/"8")
            deal_avg_price = _as_numeric_float(inner.get("trusteeshipDealAvgPrice"))
            deal_total_count = _as_int(inner.get("trusteeshipDealTotalCount"))

    return TrusteeshipDetail(
        cell_code=cell_code,
        house_del_code=_opt_str(head.get("houseDelCode")),
        resblock_name=_opt_str(head.get("resblockName")),
        house_name=_opt_str(head.get("houseName")),
        house_type_desc=_opt_str(head.get("houseTypeDesc")),
        area_text=_opt_str(head.get("area")),
        area_number=_as_float(head.get("areaNumber")),
        guide_price_yuan=_as_int(head.get("guidePrice")),
        orientation=_opt_str(head.get("orientationArr")),
        floor_type=_opt_str(head.get("floorType")),
        signal_floor=_opt_str(head.get("signalFloor")),
        total_floor=_as_int(head.get("totalFloor")),
        can_live_time=_opt_str(head.get("canLiveTime")),
        viewing_house_time=_opt_str(head.get("viewingHouseTime")),
        rent_period_desc=_opt_str(head.get("rentHousePeriodRequireDesc")),
        rent_period_desc_v2=_opt_str(head.get("rentHousePeriodRequireDescV2")),
        tg_end_date=_opt_str(head.get("tgEndDate")),
        delay_days=_as_int(head.get("delayDays")),
        tags=tuple(tags),
        manager=manager_info,
        key_desc=key_desc,
        has_smart_key=has_smart_key,
        out_show_desc=out_show_desc,
        prospects=tuple(prospects),
        house_type_images=tuple(house_type_images),
        vr_url=vr_url,
        vr_picture_url=vr_picture_url,
        hqi_score=hqi_score,
        deal_details=deal_details,
        deal_avg_price=deal_avg_price,
        deal_total_count=deal_total_count,
        fee_groups=_parse_trusteeship_fee_groups(data.get("feeInfoDto")),
        del_status=_as_int(head.get("delStatus")),
        district_name=_opt_str(head.get("districtName")),
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
        rent_mode_label=_parse_rent_mode_label(row),
        # Raw CDN originals; direct fetch is 403 for everyone — callers append
        # a size suffix (.450x/.750x/.800x/.1500x.jpg) for a public variant
        # (docs/rental-image-cdn.md). Passed through unsuffixed on purpose.
        title_image_url=_opt_str(row.get("titleImage")),
        floor_plan_image_url=_opt_str(row.get("floorPlanImage")),
    )


def _parse_rent_mode_label(row: Mapping[str, Any]) -> str | None:
    """Return the per-listing 租赁方式 without confusing it with delType.

    Live CRM rows render the mode at the start of ``title`` (for example
    ``整租·小区名称``). Some upstream variants also expose a structured
    rentType value, so prefer that when present and keep the title as a
    compatibility fallback.
    """
    aliases = {
        "001": "整租",
        "whole_rent": "整租",
        "整租": "整租",
        "002": "合租",
        "shared_rent": "合租",
        "合租": "合租",
    }
    for key in (
        "rentModeLabel", "rentTypeLabel", "rentTypeName", "rentTypeDesc",
        "rentType", "rent_type",
    ):
        value = row.get(key)
        if value is None:
            continue
        normalized = aliases.get(str(value).strip().lower())
        if normalized:
            return normalized

    title = _opt_str(row.get("mapTitle")) or _opt_str(row.get("title")) or ""
    match = re.match(r"^\s*(整租|合租)(?:\s*[·•・|｜]|\s)", title)
    return match.group(1) if match else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_numeric_float(value: Any) -> float | None:
    """float coercion that also accepts numeric strings (the 买卖 search rows
    return qualityScore as '8.78' and unitPrice as 12539.0)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
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
        # Detail-only fields from the same detailHead record (docs §房源详情).
        maintain_org=_opt_str(data.get("orgName")),
        source=_opt_str(data.get("delResourceSub")),
        floor_desc=_opt_str(data.get("floorDesc")),
        total_floors=_as_int(data.get("totalFloor")),
        listed_days=_as_int(data.get("alreadyCreateDays")),
        house_grade=_opt_str(data.get("houseGrade")),
        follow_total=_as_int(data.get("followTotal")),
        follow_last_7d=_as_int(data.get("followNum7Days")),
        showing_total=_as_int(data.get("showingTotal")),
        showing_last_7d=_as_int(data.get("showingNum7Days")),
        external_url_ke=_opt_str(data.get("keUrl")),
        external_url_lianjia=_opt_str(data.get("lianJiaUrl")),
        has_key=_as_bool(data.get("haveKey")),
        del_status_text=_opt_str(data.get("delStatusString")),
        house_id=str(data["houseId"]) if data.get("houseId") is not None else None,
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
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_positive_flag(value: Any) -> bool | None:
    """Upstream isPositive arrives as int 1/0, not JSON true/false."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return None


def _ts_to_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromtimestamp(float(value.strip()) / 1000, tz=UTC)
        except ValueError:
            return None
    return None


def _parse_prospect(body: Mapping[str, Any], listing_id: str) -> ListingProspect:
    """Parse the detail-page 实勘 record (docs §房源详情).

    An empty ``houseProspectImageList`` is the upstream's honest "not yet
    surveyed" answer for a valid 普租 house — it is NOT an error. Only a
    missing/empty ``data`` object means the id is not served by the 普租
    detail domain (e.g. 托管 ids), mirroring detailHead.
    """
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        raise UpstreamInvalidInputError(
            "no detailProspect record for this id; trusteeship (托管) listings "
            "are not covered by the 普租 detail endpoint"
        )
    photos: list[ProspectPhoto] = []
    raw_photos = data.get("houseProspectImageList")
    if isinstance(raw_photos, list):
        for item in raw_photos:
            if not isinstance(item, Mapping):
                continue
            url = _opt_str(item.get("prospectPicUrl"))
            if not url:
                continue
            photos.append(
                ProspectPhoto(
                    url=url,
                    room_name=_opt_str(item.get("roomName")),
                    image_type=_opt_str(item.get("imageType")) or "",
                    upload_user=_opt_str(item.get("uploadUserName")),
                    created_at=_ts_to_datetime(item.get("createTime")),
                )
            )
    frame = data.get("houseFrameImageResp")
    floor_plan_url = (
        _opt_str(frame.get("imageUrl")) if isinstance(frame, Mapping) else None
    )
    return ListingProspect(
        listing_id=listing_id,
        photos=tuple(photos),
        floor_plan_url=floor_plan_url,
        can_edit=_as_bool(data.get("canEditProspect")),
        has_survey_photo=any(photo.image_type == "REAL" for photo in photos),
    )


def _parse_property_info(body: Mapping[str, Any], listing_id: str) -> ListingPropertyInfo:
    """Parse detailHdicInfo — the detail page's 小区/楼栋 attributes.

    An empty/missing ``data`` means the id is not served by the 普租 detail
    domain, mirroring detailHead and detailProspect.
    """
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        raise UpstreamInvalidInputError(
            "no detailHdicInfo record for this id; trusteeship (托管) listings "
            "are not covered by the 普租 detail endpoint"
        )
    return ListingPropertyInfo(
        listing_id=listing_id,
        # 小区信息
        community=_opt_str(data.get("resblockName")),
        district=_opt_str(data.get("districtName")),
        biz_circle=_opt_str(data.get("bizCircleName")),
        tenement_fee=_opt_str(data.get("tenementFeeStr")),
        kindergarten=_clean_opt_str(data.get("kindergarten")),
        # 建筑信息
        building_type=_opt_str(data.get("buildTypeName")),
        building_structure=_opt_str(data.get("buildingStructureName")),
        building_year=(
            None if data.get("buildingYear") in (None, 0) else _as_int(data.get("buildingYear"))
        ),
        property_purpose=_opt_str(data.get("statFunctionName")),
        deal_property=_opt_str(data.get("dealPropertyName")),
        age_limit=_opt_str(data.get("propertyAgeLimitName")),
        disgust_desc=_clean_opt_str(data.get("disgustDesc")),
        haunted_desc=_clean_opt_str(data.get("hauntedDesc")),
        # 生活信息
        elevator=_opt_str(data.get("elevatorCntStr")),
        ti_hu_ratio=_opt_str(data.get("tiHuRatio")),
        water_type=_opt_str(data.get("waterTypeName")),
        electric_type=_opt_str(data.get("electricTypeName")),
        heating=_opt_str(data.get("heatingTypeName")),
        heating_fee=_clean_opt_str(data.get("heatingFeeStr")),
        gas=_opt_str(data.get("gasStr")),
        gas_fee=_clean_opt_str(data.get("gasFeeStr")),
        hot_water=_clean_opt_str(data.get("hotWaterStr")),
        hot_water_fee=_clean_opt_str(data.get("hotWaterFeeStr")),
        middle_water=_clean_opt_str(data.get("middleWaterStr")),
        middle_water_fee=_clean_opt_str(data.get("middleWaterFeeStr")),
        parking_ratio=_opt_str(data.get("carRatio")),
        parking_fee=_opt_str(data.get("parkingFee")),
        parking_above_ground=_clean_opt_str(data.get("carUpCntStr")),
        parking_underground=_clean_opt_str(data.get("carDownCntStr")),
        green_rate=_as_float(data.get("greenRate")),
        cubage_rate=_as_float(data.get("cubageRate")),
    )


def _parse_house_labels(body: Mapping[str, Any]) -> tuple[str, ...]:
    """Parse getHouseLabel — a plain list of label strings.

    An empty list is a valid answer (verified live: some houses only carry
    a couple of labels, some none).
    """
    raw = body.get("data")
    if not isinstance(raw, list):
        return ()
    labels = [str(item) for item in raw if isinstance(item, str) and item]
    return tuple(dict.fromkeys(labels))


def _parse_hqi_score(body: Mapping[str, Any], listing_id: str) -> HqiScore | None:
    """Parse detailHqiTab — the HQI quality-score record.

    An empty ``data`` object is the upstream's honest "no HQI record yet"
    answer (verified live: 106128807039 returns ``{}`` while
    106128274229 returns a full record), so it maps to ``None`` rather
    than an error — unlike detailHead where empty data means "unknown id".
    """
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        return None

    heat_items: list[HqiHeatItem] = []
    raw_heat = data.get("chotDataList")
    if isinstance(raw_heat, list):
        for item in raw_heat:
            if not isinstance(item, Mapping):
                continue
            name = _opt_str(item.get("dataName"))
            if not name:
                continue
            heat_items.append(
                HqiHeatItem(
                    name=name,
                    value=_opt_str(item.get("dataValue")),
                    fluctuate=_opt_str(item.get("fluctuateVal")),
                    positive=_as_positive_flag(item.get("isPositive")),
                )
            )

    suggestions: list[HqiSuggestion] = []
    raw_suggestions = data.get("optimizeSuggestionList")
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions:
            if not isinstance(item, Mapping):
                continue
            suggestions.append(
                HqiSuggestion(
                    item=_opt_str(item.get("optimizeItemName")),
                    suggestion=_opt_str(item.get("suggestionDesc")),
                )
            )

    rank_prefix = _opt_str(data.get("rankDescPrefix")) or ""
    rank_suffix = _opt_str(data.get("rankDescSuffix")) or ""
    rank_text = f"{rank_prefix}{rank_suffix}" if (rank_prefix or rank_suffix) else None
    return HqiScore(
        total_score=_opt_str(data.get("totalScoreValue")),
        level=_opt_str(data.get("totalScoreLevel")),
        next_level=_opt_str(data.get("nextLevelName")),
        rank_text=rank_text,
        pending_optimize=_opt_str(data.get("pendingOptimizeDesc")),
        heat_items=tuple(heat_items),
        suggestions=tuple(suggestions),
    )


def _parse_maintain_info(body: Mapping[str, Any], listing_id: str) -> ListingMaintainInfo:
    """Parse getMaintainInfo — the detail page's 维护信息 section.

    An empty/missing ``data`` means the id is not served by the 普租 detail
    domain (probed 2026-08-09: unknown ids return code=100001 房源编码错误),
    mirroring detailHead. Modules carry the upstream-rendered display values
    (e.g. 装修情况 = "精装"), so no code mapping is needed downstream.
    """
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        raise UpstreamInvalidInputError(
            "no getMaintainInfo record for this id; trusteeship (托管) listings "
            "are not covered by the 普租 detail endpoint"
        )
    modules: list[MaintainModule] = []
    for raw_module in ("importantModules", "otherModules"):
        raw_list = data.get(raw_module)
        if not isinstance(raw_list, list):
            continue
        for module in raw_list:
            if not isinstance(module, Mapping):
                continue
            fields: list[MaintainField] = []
            raw_fields = module.get("fields")
            if isinstance(raw_fields, list):
                for field in raw_fields:
                    if not isinstance(field, Mapping):
                        continue
                    name = _opt_str(field.get("fieldName"))
                    if not name:
                        continue
                    fields.append(
                        MaintainField(
                            name=name,
                            display_value=_opt_str(field.get("displayValue")),
                            complete=_as_bool(field.get("complete")),
                        )
                    )
            modules.append(
                MaintainModule(
                    rate_text=_opt_str(module.get("completenessRate")),
                    fields=tuple(fields),
                )
            )
    return ListingMaintainInfo(
        listing_id=listing_id,
        modules=tuple(modules),
        remark=_opt_str(data.get("remark")),
        all_field_rate=_as_int(data.get("allFieldMaintainRate")),
        important_rate=_as_int(data.get("importantFieldMaintainRate")),
        owner_lowest_price=_opt_str(data.get("ownerLowestPrice")),
    )


def _parse_follow_records(body: Mapping[str, Any]) -> tuple[FollowRecord, ...]:
    """Parse detailFollow — the detail page's 跟进记录 list.

    ``data.result`` is ``None`` when the house has no follow-ups yet
    (verified live: 106128807039 returns totalCount=0 with result=None),
    which maps to an empty tuple — a valid answer, not an error.
    """
    data = body.get("data")
    if not isinstance(data, Mapping):
        return ()
    raw_result = data.get("result")
    if not isinstance(raw_result, list):
        return ()
    records: list[FollowRecord] = []
    for item in raw_result:
        if not isinstance(item, Mapping):
            continue
        content = _opt_str(item.get("followUpContent"))
        if not content:
            continue
        labels: list[str] = []
        raw_labels = item.get("followLabel")
        if isinstance(raw_labels, list):
            labels = [str(label) for label in raw_labels if str(label)]
        records.append(
            FollowRecord(
                content=content,
                follow_type=_opt_str(item.get("followTypeStr")),
                creator_name=_opt_str(item.get("creatorName")),
                role=_opt_str(item.get("roleTypeStr")),
                created_at=_ts_to_datetime(item.get("createTime")),
                labels=tuple(dict.fromkeys(labels)),
                label_code=_opt_str(item.get("followLabelCode")),
                remarks=_clean_opt_str(item.get("remarks")),
                on_top=bool(item.get("onTop")),
                on_top_time=_ts_to_datetime(item.get("onTopTime")),
            )
        )
    return tuple(records)


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


def _clean_opt_str(value: Any) -> str | None:
    """_opt_str that also collapses the upstream's empty-string "no value"
    sentinel (e.g. detailHdicInfo.heatingFeeStr is '' when 无供暖费用)."""
    text = _opt_str(value)
    return text if text else None


def _id_value(value: Any) -> str | None:
    """Filter-catalog id coercion: the root groups carry id='' and the
    catalogue's 不限 entries id='-1'; both are not real values."""
    text = _opt_str(value)
    if not text or text == "-1":
        return None
    return text


def _sale_business_error(body: Mapping[str, Any]) -> None:
    """Raise for non-success 买卖 (house.link) business envelopes.

    The house domain uses code=1 for success (not the lease domain's
    100000); 403/31002/未登录 are handled by the session provider's
    auth-failure detection before we ever parse the body.
    """
    code = body.get("code")
    if code in (1, "1", 0, "0"):
        return
    if code in (403, "403", 31002, "31002") or (
        isinstance(body.get("msg"), str)
        and ("未登录" in body["msg"] or "请先登录" in body["msg"])
    ):
        raise AuthenticationRequiredError("CRM 买卖 authorization was rejected")
    if code == 100001 or code == "100001":
        raise UpstreamInvalidInputError(str(body.get("msg") or "house upstream rejected input"))
    raise UpstreamChangedError(
        f"house upstream returned unknown business code={code!r} msg={body.get('msg')!r}"
    )


def _parse_sale_filter_option(row: Mapping[str, Any]) -> SaleListingFilterOption:
    raw_children = row.get("children") or []
    children: list[SaleListingFilterOption] = []
    if isinstance(raw_children, list):
        for child in raw_children:
            if not isinstance(child, Mapping):
                continue
            if row.get("key") == "select":
                # Nested select dropdowns: child has its own key and children.
                children.append(_parse_sale_filter_option(child))
            else:
                children.append(
                    SaleListingFilterOption(
                        key=None,
                        name=_opt_str(child.get("name")) or "",
                        value=_id_value(child.get("id")),
                        selection_type="",
                        default_value=None,
                        for_show=False,
                        ext={},
                        children=(),
                    )
                )
    return SaleListingFilterOption(
        key=_opt_str(row.get("key")),
        name=_opt_str(row.get("name")) or "",
        value=_id_value(row.get("id")),
        selection_type=_opt_str(row.get("type")) or "",
        default_value=(
            _id_value(row["defaultValue"].get("id"))
            if isinstance(row.get("defaultValue"), Mapping)
            else None
        ),
        for_show=bool(row.get("forShow")),
        ext=dict(row["ext"]) if isinstance(row.get("ext"), dict) else {},
        children=tuple(children),
    )


def _parse_sale_filter_options(body: Mapping[str, Any]) -> tuple[SaleListingFilterOption, ...]:
    raw_options = body.get("data")
    if not isinstance(raw_options, list):
        raise UpstreamChangedError("sale filter-options response 'data' is not an array")
    return tuple(
        _parse_sale_filter_option(option)
        for option in raw_options
        if isinstance(option, Mapping)
    )


def _parse_sale_listing(row: Mapping[str, Any]) -> SaleListing:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    floor_type = _opt_str(row.get("floorType"))
    total_floor = _as_int(row.get("totalFloor"))
    floor_desc: str | None
    if floor_type and total_floor is not None:
        floor_desc = f"{floor_type}/{total_floor}"
    else:
        floor_desc = _opt_str(row.get("showFloor"))
    return SaleListing(
        listing_id=_opt_str(row.get("houseDelCode")) or "",
        community=_opt_str(row.get("communityName")) or "",
        biz_circle=_opt_str(row.get("bizCircleName")),
        layout=_opt_str(row.get("unitType")),
        area_sqm=_as_float(row.get("areaSize")),
        total_price_yuan=_as_float(row.get("totalPrice")),
        total_price_text=_opt_str(row.get("totalPriceStr")),
        unit_price_yuan_per_sqm=_as_float(row.get("unitPrice")),
        floor_desc=floor_desc,
        floor_type=floor_type,
        orientation=_opt_str(row.get("orientation")),
        tags=tuple(dict.fromkeys(str(tag) for tag in tags if str(tag))),
        visit_count_15d=_as_int(row.get("visitCount")),
        follow_up=row.get("followUp") if isinstance(row.get("followUp"), bool) else None,
        create_time=_ts_to_datetime(row.get("createTime")),
        maintainer_name=_opt_str(row.get("maintainerName")),
        maintainer_tag=_opt_str(row.get("maintainerTag")),
        maintain_percentage=_as_int(row.get("maintainPercentage")),
        quality_score=_as_numeric_float(row.get("qualityScore")),
        holder_level=_opt_str(row.get("holderLevel")),
        del_type=_as_int(row.get("delType")),
        community_id=_opt_str(row.get("communityId")),
        payment_mode=_opt_str(row.get("paymentMode")),
        stat_function=_opt_str(row.get("statFunction")),
        subway_line=_opt_str(row.get("subwayLineName")),
        subway_station=_opt_str(row.get("subwayName")),
        vr_status=_as_int(row.get("vrStatus")),
        surface_image_url=_opt_str(row.get("surfaceImage")),
        floor_plan_image_url=_opt_str(row.get("floorPlanImage")),
    )


def _parse_sale_page(
    body: Mapping[str, Any], filters: SaleListingFilters, request_id: str
) -> SaleListingPage:
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise UpstreamChangedError("sale search response missing 'data' object")
    raw_list = data.get("list")
    if raw_list is None:
        # totalCount=0 with list=null is the upstream's honest empty answer
        # (verified live: a multi_community_id query with no visible listings).
        raw_list = []
    if not isinstance(raw_list, list):
        raise UpstreamChangedError("sale search response 'data.list' is not an array")
    items = tuple(_parse_sale_listing(row) for row in raw_list if isinstance(row, Mapping))
    total = _as_int(data.get("totalCount")) or 0
    page = _as_int(data.get("currentPage")) or filters.page
    total_page = _as_int(data.get("totalPage"))
    has_more = total_page is not None and page < total_page
    return SaleListingPage(
        items=items,
        page=page,
        total=total,
        has_more=has_more,
        request_id=request_id,
    )


def _parse_sale_suggestions(body: Mapping[str, Any]) -> tuple[SaleCommunitySuggestion, ...]:
    raw = body.get("data")
    if not isinstance(raw, list):
        raise UpstreamChangedError("sale suggest response 'data' is not an array")
    suggestions: list[SaleCommunitySuggestion] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        text = _opt_str(row.get("text"))
        community_id = _opt_str(row.get("communityId"))
        if not text or not community_id:
            continue
        suggestions.append(
            SaleCommunitySuggestion(
                text=text,
                community_id=community_id,
                resblock_name=_opt_str(row.get("resblockName")),
                resblock_alias=_opt_str(row.get("resblockAlias")),
                district_name=_opt_str(row.get("districtName")),
                bizcircle_name=_opt_str(row.get("bizcircleName")),
                house_count=_as_int(row.get("houseCount")),
                del_type=_opt_str(row.get("delType")),
            )
        )
    return tuple(suggestions)


def _parse_sale_detail(
    views_body: Mapping[str, Any], ext_body: Mapping[str, Any], listing_id: str
) -> SaleListingDetail:
    data = views_body.get("data")
    if not isinstance(data, Mapping) or not data:
        raise UpstreamInvalidInputError(
            f"no housedel/views record for {listing_id}; the id is not served by the 买卖 detail domain"
        )
    head = data.get("housedelBaseInfo")
    basic = data.get("basicInfo")
    if not isinstance(head, Mapping):
        raise UpstreamChangedError("housedel/views 'data.housedelBaseInfo' is not an object")
    if not isinstance(basic, Mapping):
        basic = {}
    holder = head.get("holderInfo")
    ext_raw = ext_body.get("data")
    ext_data = ext_raw if isinstance(ext_raw, Mapping) else {}
    head_get = head.get
    basic_get = basic.get
    ext_get = ext_data.get
    return SaleListingDetail(
        listing_id=listing_id,
        display_name=_opt_str(head_get("displayName")),
        display_price=_opt_str(head_get("displayPrice")),
        latest_price_yuan=_as_float(head_get("latestPrice")),
        unit_price_text=_opt_str(head_get("unitPrice")),
        area_sqm=_as_float(head_get("area")),
        bedroom_amount=_as_int(head_get("bedroomAmount")),
        parlor_amount=_as_int(head_get("parlorAmount")),
        toilet_amount=_as_int(head_get("toiletAmount")),
        cookroom_amount=_as_int(head_get("cookroomAmount")),
        display_floor=_opt_str(head_get("displayFloor")),
        orientation=_opt_str(head_get("orientation")),
        del_grade=_opt_str(head_get("delGrade")),
        broker_grade=_opt_str(head_get("brokerGrade")),
        holder_name=_opt_str(holder.get("name")) if isinstance(holder, Mapping) else None,
        holder_org=_opt_str(holder.get("orgName")) if isinstance(holder, Mapping) else None,
        last_days=_opt_str(head_get("lastDays")),
        ctime=_opt_str(head_get("ctime")),
        house_origin=_opt_str(head_get("houseOrigin")),
        house_id=_opt_str(head_get("houseId")),
        acn_house_id=_opt_str(head_get("acnHouseId")),
        resblock_id=_opt_str(head_get("resblockId")),
        res_block_info=_opt_str(head_get("resBlockInfo")),
        vr_status=_as_int(head_get("vrStatus")),
        owner_reserve_price=_opt_str(head_get("ownerReservePrice")),
        inventory_score=_opt_str(head_get("inventoryScore")),
        del_status=_as_int(head_get("housedelStatus")),
        is_credential_completed=(
            head_get("isCredentialCompleted")
            if isinstance(head_get("isCredentialCompleted"), bool)
            else None
        ),
        district_name=_opt_str(basic_get("districtName")),
        biz_circle=_opt_str(basic_get("bizcircleName")),
        build_year=_as_int(basic_get("buildYear")),
        build_type=_opt_str(basic_get("buildType")),
        build_struct=_opt_str(basic_get("buildStruct")),
        deal_prop=_opt_str(basic_get("dealProp")),
        house_usage=_opt_str(basic_get("houseUsage")),
        tenement_fee=_opt_str(basic_get("tenementFee")),
        heat_fee=_opt_str(basic_get("heatFee")),
        gas_fee=_opt_str(basic_get("gasFee")),
        water_type=_opt_str(basic_get("waterType")),
        electric_type=_opt_str(basic_get("eletricType")),
        heat_type=_opt_str(basic_get("heatType")),
        has_gas=_opt_str(basic_get("hasGas")),
        has_hot_water=_opt_str(basic_get("hasHotWater")),
        has_mid_water=_opt_str(basic_get("hasMidWater")),
        mid_water_fee=_opt_str(basic_get("midWaterFee")),
        hot_water_fee=_opt_str(basic_get("hotWaterFee")),
        car_ratio=_opt_str(basic_get("carRatio")),
        car_onground=_as_int(basic_get("carOnground")),
        car_underground=_as_int(basic_get("carUnderground")),
        park_fee=_opt_str(basic_get("parkFee")),
        has_lift=_opt_str(basic_get("hasLift")),
        lift_house_ratio=_opt_str(basic_get("liftHouseRatio")),
        school_info=_opt_str(basic_get("schoolInfo")),
        prop_years=_opt_str(basic_get("propYears")),
        building_disgust=_opt_str(basic_get("buildingDisgust")),
        external_url_lianjia=_opt_str(ext_get("lianjiaUrl")),
        external_url_beike=_opt_str(ext_get("beikeUrl")),
        vr_url=_opt_str(ext_get("vrUrl")),
        net_work_status=_as_int(ext_get("netWorkStatus")),
    )


def _parse_sale_maintain_field(field: Mapping[str, Any]) -> SaleMaintainField:
    return SaleMaintainField(
        key=_opt_str(field.get("key")) or "",
        name=_opt_str(field.get("name")) or "",
        value=_opt_str(field.get("value")),
        important=bool(field.get("important")),
        comment=_opt_str(field.get("comment")),
    )


def _parse_sale_maintain_info(
    body: Mapping[str, Any], listing_id: str
) -> SaleMaintainInfo:
    data = body.get("data")
    if not isinstance(data, Mapping) or not data:
        raise UpstreamInvalidInputError(
            f"no getMaintainInfo record for {listing_id}; the id is not served by the 买卖 detail domain"
        )
    basic = data.get("maintainBasicInfo")
    if not isinstance(basic, Mapping):
        basic = {}
    modules: list[SaleMaintainModule] = []
    raw_modules = basic.get("maintainList")
    if isinstance(raw_modules, list):
        for module in raw_modules:
            if not isinstance(module, Mapping):
                continue
            fields: list[SaleMaintainField] = []
            for row in module.get("row") or []:
                if not isinstance(row, Mapping):
                    continue
                for field_list in row.get("list") or []:
                    if isinstance(field_list, Mapping):
                        fields.append(_parse_sale_maintain_field(field_list))
            modules.append(
                SaleMaintainModule(
                    name=_opt_str(module.get("name")) or "",
                    fields=tuple(fields),
                )
            )
    important: list[SaleMaintainField] = []
    important_info = data.get("importantBasicInfo")
    if isinstance(important_info, Mapping):
        for field in important_info.get("importantList") or []:
            if isinstance(field, Mapping):
                important.append(_parse_sale_maintain_field(field))
    return SaleMaintainInfo(
        listing_id=listing_id,
        modules=tuple(modules),
        important_fields=tuple(important),
        complete_rate=_opt_str(basic.get("completeRate")),
        last_update_time=_ts_to_datetime(important_info.get("lastUpdateTime"))
        if isinstance(important_info, Mapping)
        else None,
        remark=None,
    )


def _parse_sale_follows(body: Mapping[str, Any]) -> tuple[SaleFollowRecord, ...]:
    data = body.get("data")
    if not isinstance(data, Mapping):
        return ()
    raw_list = data.get("list")
    if not isinstance(raw_list, list):
        return ()
    records: list[SaleFollowRecord] = []
    for row in raw_list:
        if not isinstance(row, Mapping):
            continue
        records.append(
            SaleFollowRecord(
                follow_id=_as_int(row.get("id")),
                content=_opt_str(row.get("followContent")),
                creator_name=_opt_str(row.get("creatorName")),
                create_time=_opt_str(row.get("createTime")),
                on_top=bool(row.get("onTop")),
                remarks=_opt_str(row.get("remarks")),
                follow_label=_opt_str(row.get("followLabel")),
                video_url=_opt_str(row.get("videoUrl")),
            )
        )
    return tuple(records)


def _parse_sale_map_suggestions(body: Mapping[str, Any]) -> tuple[SaleMapSuggestion, ...]:
    raw = body.get("data")
    if not isinstance(raw, list):
        raise UpstreamChangedError("sale map suggest response 'data' is not an array")
    suggestions: list[SaleMapSuggestion] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        text = _opt_str(row.get("text"))
        suggestion_id = _opt_str(row.get("id"))
        if not text or not suggestion_id:
            continue
        suggestions.append(
            SaleMapSuggestion(
                suggestion_id=suggestion_id,
                text=text,
                alias=_opt_str(row.get("alias")),
                bizcircle_name=_opt_str(row.get("bizcircleName")),
                district_name=_opt_str(row.get("districtName")),
                item_type=_opt_str(row.get("type")) or "",
                count=_as_int(row.get("count")),
                latitude=_as_numeric_float(row.get("latitude")),
                longitude=_as_numeric_float(row.get("longitude")),
                unit_price=_as_numeric_float(row.get("unitPrice")),
            )
        )
    return tuple(suggestions)


def _parse_sale_map_bubbles(
    body: Mapping[str, Any], group_type: str
) -> tuple[SaleMapBubble, ...]:
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise UpstreamChangedError("sale map bubble response missing 'data' object")
    raw = data.get("list")
    if not isinstance(raw, Mapping):
        raise UpstreamChangedError("sale map bubble response 'data.list' is not an object")
    bubbles: list[SaleMapBubble] = []
    for row in raw.values():
        if not isinstance(row, Mapping):
            continue
        bubble_id = _opt_str(row.get("id"))
        name = _opt_str(row.get("name"))
        if not bubble_id or not name:
            continue
        bubbles.append(
            SaleMapBubble(
                bubble_id=bubble_id,
                name=name,
                count=_as_int(row.get("count")),
                unit_price=_as_numeric_float(row.get("unit_price")),
                latitude=_as_numeric_float(row.get("latitude")),
                longitude=_as_numeric_float(row.get("longitude")),
                desc=_opt_str(row.get("desc")),
            )
        )
    return tuple(bubbles)


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

    def get_rental_listing_redirect_url(self, listing_id: str) -> str | None:
        """Return the trusteeship ``cell_code`` for a managed (del_type=5) row.

        Uses the same getRedirectUrl endpoint the 房源列表 page calls when an
        employee clicks a managed row: the response ``data`` is the
        trusteeship detail URL and its last path segment is the cell_code.
        Returns None when the upstream has no redirect (e.g. a 普租 row) or
        the URL carries no usable tail.
        """
        request = _build_redirect_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.get_redirect_url returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        url = body.get("data")
        if not isinstance(url, str) or not url.strip():
            return None
        tail = url.rstrip("/").split("/")[-1]
        return tail or None

    def get_rental_listing_prospect(self, listing_id: str) -> ListingProspect:
        """Return the detail-page 实勘 record (photos, floor plan, VR flags).

        An empty photo list is a valid answer — it means the house has not
        been surveyed yet.
        """
        request = _build_detail_prospect_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"rental_listing.detail_prospect returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        return _parse_prospect(body, listing_id)

    def get_rental_listing_house_info(
        self, listing_id: str, *, include_follows: bool = True
    ) -> ListingDetailInfo:
        """Return the aggregated detail-page information beyond detailHead.

        Four or five upstream records: getHouseLabel (labels), detailHdicInfo
        (小区/楼栋 attributes), detailHqiTab (HQI score), getMaintainInfo
        (维护信息), and optionally detailFollow (跟进记录). Customer-facing
        flows pass ``include_follows=False`` so the sensitive route is never
        requested. HQI may be None for houses
        without a score record and follows may be empty when no follow-up
        exists; the hdicInfo call must succeed for a valid 普租 id, otherwise
        the unknown-id error surfaces from it.
        """
        request = _build_hdic_info_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"{request.route} returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        property_info = _parse_property_info(body, listing_id)

        request = _build_house_label_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"{request.route} returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        labels = _parse_house_labels(body)

        request = _build_hqi_tab_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"{request.route} returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        hqi = _parse_hqi_score(body, listing_id)

        request = _build_maintain_info_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"{request.route} returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _raise_for_business_code(body)
        maintain = _parse_maintain_info(body, listing_id)

        follows = ()
        if include_follows:
            request = _build_follow_request(listing_id)
            response = self._session.authorized_fetch(request)
            if response.status_code != 200:
                raise UpstreamChangedError(
                    f"{request.route} returned status {response.status_code}"
                )
            body = _coerce_mapping(response.body)
            _raise_for_business_code(body)
            follows = _parse_follow_records(body)

        return ListingDetailInfo(
            listing_id=listing_id,
            labels=labels,
            property_info=property_info,
            hqi=hqi,
            maintain=maintain,
            follows=follows,
        )

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

    # -- 托管 (省心租, trusteeship.link.lianjia.com) --------------------------

    def get_trusteeship_detail(self, cell_code: str) -> TrusteeshipDetail:
        request = _build_trusteeship_detail_request(cell_code)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"trusteeship.get_detail returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        return _parse_trusteeship_detail(body, cell_code)

    def get_trusteeship_deals(
        self, cell_code: str, *, page: int, page_size: int
    ) -> TrusteeshipDealPage:
        request = _build_trusteeship_deals_request(
            cell_code, page=page, page_size=page_size
        )
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"trusteeship.get_deals returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        return _parse_trusteeship_deals(
            body, page=page, request_id=request.request_id
        )

    def search_trusteeship_listings(
        self, *, page: int, page_size: int, cell_code: str | None = None
    ) -> TrusteeshipListingPage:
        request = _build_trusteeship_list_request(
            page=page, page_size=page_size, cell_code=cell_code
        )
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"trusteeship.search_listings returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        return _parse_trusteeship_listings(
            body, page=page, page_size=page_size, request_id=request.request_id
        )

    # -- 买卖 (sale, house.link) --------------------------------------------

    def search_sale_listings(self, filters: SaleListingFilters) -> SaleListingPage:
        request = _build_sale_search_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.search returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_page(body, filters, request.request_id)

    def sale_filter_options(self) -> tuple[SaleListingFilterOption, ...]:
        request = _build_sale_filter_options_request()
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.filter_options returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_filter_options(body)

    def sale_community_suggest(self, query: str) -> tuple[SaleCommunitySuggestion, ...]:
        request = _build_sale_suggest_request(query)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.suggest returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_suggestions(body)

    def get_sale_listing_detail(self, listing_id: str) -> SaleListing:
        # 买卖 detailHead equivalent: searchQueryNew with del_code returns the
        # single row; a missing row means the id is not visible to the caller.
        request = _build_sale_search_request(
            SaleListingFilters(
                # A housedelCode can come from any permission-visible pool
                # (including sale_map_nearby_search, whose scope is ``all``).
                # ``all`` still remains upstream permission-scoped; it only
                # avoids incorrectly restricting a detail lookup to the
                # maintenance pool.
                scope="all",
                community_ids=(),
                district_id=None,
                listing_id=listing_id,
                price_wan=None,
                area_sqm=None,
                rooms=(),
                floors=(),
                orientations=(),
                house_layouts=(),
                tags=(),
                select=(),
                house_age=None,
                visitable_times=None,
                payment_mode=None,
                building_type=None,
                sort="period1_desc_createtime_desc",
                page=1,
            )
        )
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.search returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        data = body.get("data")
        raw_list = data.get("list") if isinstance(data, Mapping) else None
        if not isinstance(raw_list, list) or not raw_list:
            raise UpstreamInvalidInputError(
                f"no search row for 买卖 listing id {listing_id}; the id is "
                "not visible to the caller or belongs to another domain"
            )
        return _parse_sale_listing(raw_list[0])

    def get_sale_listing_detail_head(self, listing_id: str) -> SaleListingDetail:
        views_request = _build_sale_detail_request(listing_id)
        views_response = self._session.authorized_fetch(views_request)
        if views_response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.get_detail returned status {views_response.status_code}"
            )
        views_body = _coerce_mapping(views_response.body)
        _sale_business_error(views_body)

        ext_request = _build_sale_ext_info_request(listing_id)
        ext_response = self._session.authorized_fetch(ext_request)
        if ext_response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.get_ext_info returned status {ext_response.status_code}"
            )
        ext_body = _coerce_mapping(ext_response.body)
        _sale_business_error(ext_body)
        return _parse_sale_detail(views_body, ext_body, listing_id)

    def get_sale_listing_maintain_info(self, listing_id: str) -> SaleMaintainInfo:
        request = _build_sale_maintain_info_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.get_maintain_info returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_maintain_info(body, listing_id)

    def get_sale_listing_follows(self, listing_id: str) -> tuple[SaleFollowRecord, ...]:
        request = _build_sale_follow_request(listing_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_listing.get_follow returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_follows(body)

    def sale_map_suggest(self, query: str, city_id: str) -> tuple[SaleMapSuggestion, ...]:
        request = _build_sale_map_suggest_request(query, city_id)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_map.suggest returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_map_suggestions(body)

    def sale_map_bubbles(
        self, filters: SaleMapBubbleFilters
    ) -> tuple[SaleMapBubble, ...]:
        request = _build_sale_map_bubbles_request(filters)
        response = self._session.authorized_fetch(request)
        if response.status_code != 200:
            raise UpstreamChangedError(
                f"sale_map.bubbles returned status {response.status_code}"
            )
        body = _coerce_mapping(response.body)
        _sale_business_error(body)
        return _parse_sale_map_bubbles(body, filters.group_type)


# satisfy static Protocol check without runtime isinstance
_CrmClientProtocol = CrmClientProtocol
