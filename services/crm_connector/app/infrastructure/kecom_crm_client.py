from __future__ import annotations

import logging
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

    def get_rental_listing_house_info(self, listing_id: str) -> ListingDetailInfo:
        """Return the aggregated detail-page information beyond detailHead.

        Five upstream records: getHouseLabel (labels), detailHdicInfo
        (小区/楼栋 attributes), detailHqiTab (HQI score), getMaintainInfo
        (维护信息), detailFollow (跟进记录). HQI may be None for houses
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


# satisfy static Protocol check without runtime isinstance
_CrmClientProtocol = CrmClientProtocol
