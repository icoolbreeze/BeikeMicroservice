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
    _build_detail_prospect_request,
    _build_detail_request,
    _build_hdic_info_request,
    _build_house_label_request,
    _build_hqi_tab_request,
    _build_search_request,
    _build_whoami_request,
    _build_map_bubbles_request,
    _build_map_search_request,
    _build_map_suggest_request,
    _parse_follow_records,
    _parse_map_bubbles,
    _parse_map_page,
    _parse_map_suggestions,
    _parse_house_labels,
    _parse_hqi_score,
    _parse_listing,
    _parse_maintain_info,
    _parse_page,
    _parse_principal,
    _parse_property_info,
    _parse_prospect,
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


def test_build_detail_prospect_request_uses_prospect_route() -> None:
    request = _build_detail_prospect_request("RC123456")
    assert request.route == "rental_listing.detail_prospect"
    assert request.method == "GET"
    assert request.query == {"delCode": "RC123456"}
    assert request.body is None


def test_parse_prospect_maps_photos_floor_plan_and_flags() -> None:
    body = {
        "code": 100000, "msg": "加载成功",
        "data": {
            "canEditProspect": False,
            "houseFrameImageResp": {"imageUrl": "https://img.ke.com/huxing.png"},
            "houseProspectImageList": [
                {"prospectPicUrl": "https://img.ke.com/real-1.jpg",
                 "roomName": "客厅", "imageType": "REAL",
                 "uploadUserName": "张三", "createTime": 1750000000000},
                {"prospectPicUrl": "https://img.ke.com/title.jpg",
                 "roomName": None, "imageType": "TITLE",
                 "uploadUserName": None, "createTime": 1750000500000},
            ],
        },
    }
    prospect = _parse_prospect(body, "106128762229")

    assert prospect.listing_id == "106128762229"
    assert len(prospect.photos) == 2
    photo = prospect.photos[0]
    assert photo.url == "https://img.ke.com/real-1.jpg"
    assert photo.room_name == "客厅"
    assert photo.image_type == "REAL"
    assert photo.upload_user == "张三"
    assert photo.created_at is not None and photo.created_at.year == 2025
    assert prospect.floor_plan_url == "https://img.ke.com/huxing.png"
    assert prospect.can_edit is False
    assert prospect.has_survey_photo is True


def test_parse_prospect_empty_photo_list_means_not_surveyed() -> None:
    # A valid 普租 house with no survey yet returns non-empty data with an
    # empty image list — this is NOT an error (verified against the live
    # upstream: 106128814453).
    body = {
        "code": 100000, "msg": "加载成功",
        "data": {
            "canEditProspect": False, "houseFrameImageResp": {},
            "houseProspectImageList": [], "houseProspectRoomList": [],
        },
    }
    prospect = _parse_prospect(body, "106128814453")

    assert prospect.photos == ()
    assert prospect.has_survey_photo is False
    assert prospect.floor_plan_url is None


def test_parse_prospect_raises_when_data_empty() -> None:
    # Empty data mirrors detailHead: the id is not served by the 普租 domain.
    with pytest.raises(UpstreamInvalidInputError):
        _parse_prospect({"code": 100000, "data": {}}, "10611245074901")


def test_build_house_info_requests_use_detail_page_routes() -> None:
    hdic = _build_hdic_info_request("RC-1")
    assert hdic.route == "rental_listing.get_hdic_info"
    assert hdic.query == {"delCode": "RC-1"}

    label = _build_house_label_request("RC-1")
    assert label.route == "rental_listing.get_house_label"
    assert label.query == {"delCode": "RC-1"}

    # isApp=false is required by the upstream (缺少必要的入参 without it).
    hqi = _build_hqi_tab_request("RC-1")
    assert hqi.route == "rental_listing.get_hqi_tab"
    assert hqi.query == {"delCode": "RC-1", "isApp": "false"}


def test_parse_property_info_maps_hdic_attributes() -> None:
    # body shape from the live detailHdicInfo capture (106128274229)
    body = {
        "code": 100000,
        "data": {
            "resblockName": "成发紫东阳光", "districtName": "成华",
            "bizCircleName": "新华公园", "buildTypeName": "塔楼",
            "buildingStructureName": "框架结构", "buildingYear": 2015,
            "statFunctionName": "普通住宅", "dealPropertyName": "商品房",
            "elevatorCntStr": "有", "tiHuRatio": "2梯5户",
            "tenementFeeStr": "2.15", "waterTypeName": "民水",
            "electricTypeName": "民电", "gasStr": "有",
            "heatingTypeName": None, "carRatio": "1:0.48",
            "parkingFee": "350", "greenRate": 26, "cubageRate": 4.6,
            "propertyAgeLimitName": "70",
            "disgustDesc": None, "hauntedDesc": "高压线",
            "heatingFeeStr": "", "gasFeeStr": "2.03", "hotWaterStr": "有",
            "hotWaterFeeStr": "0.0", "middleWaterStr": "无", "middleWaterFeeStr": "0.0",
            "carUpCntStr": "无", "carDownCntStr": "361", "kindergarten": "成华区第三幼儿园",
        },
    }
    info = _parse_property_info(body, "106128274229")

    assert info.listing_id == "106128274229"
    assert info.community == "成发紫东阳光"
    assert info.district == "成华"
    assert info.biz_circle == "新华公园"
    assert info.building_type == "塔楼"
    assert info.building_structure == "框架结构"
    assert info.building_year == 2015
    assert info.property_purpose == "普通住宅"
    assert info.deal_property == "商品房"
    assert info.elevator == "有"
    assert info.ti_hu_ratio == "2梯5户"
    assert info.tenement_fee == "2.15"
    assert info.water_type == "民水"
    assert info.electric_type == "民电"
    assert info.gas == "有"
    assert info.heating is None
    assert info.parking_ratio == "1:0.48"
    assert info.parking_fee == "350"
    assert info.green_rate == 26.0
    assert info.cubage_rate == 4.6
    assert info.age_limit == "70"
    # 建筑信息补充
    assert info.disgust_desc is None
    assert info.haunted_desc == "高压线"
    # 生活信息补充（空串 -> None）
    assert info.heating_fee is None
    assert info.gas_fee == "2.03"
    assert info.hot_water == "有"
    assert info.hot_water_fee == "0.0"
    assert info.middle_water == "无"
    assert info.middle_water_fee == "0.0"
    assert info.parking_above_ground == "无"
    assert info.parking_underground == "361"
    assert info.kindergarten == "成华区第三幼儿园"


def test_parse_property_info_normalizes_missing_year() -> None:
    # buildingYear=0 is the upstream's "unknown" sentinel, not year zero.
    body = {"code": 100000, "data": {"resblockName": "双桥路南一街", "buildingYear": 0}}
    info = _parse_property_info(body, "RC-1")
    assert info.building_year is None
    assert info.community == "双桥路南一街"


def test_parse_property_info_raises_when_data_empty() -> None:
    # Mirrors detailHead/detailProspect: unknown id (e.g. 托管) -> invalid input.
    with pytest.raises(UpstreamInvalidInputError):
        _parse_property_info({"code": 100000, "data": {}}, "10611245074901")


def test_parse_house_labels_extracts_deduplicated_list() -> None:
    body = {"code": 100000, "data": ["电梯房", "VR房", "钥", "学区房", "电梯房"]}
    labels = _parse_house_labels(body)
    assert labels == ("电梯房", "VR房", "钥", "学区房")


def test_parse_house_labels_empty_list_is_valid() -> None:
    assert _parse_house_labels({"code": 100000, "data": []}) == ()
    assert _parse_house_labels({"code": 100000, "data": None}) == ()


def test_parse_hqi_score_maps_score_heat_and_suggestions() -> None:
    # body shape from the live detailHqiTab capture (106128274229)
    body = {
        "code": 100000,
        "data": {
            "totalScoreValue": "38", "totalScoreLevel": None,
            "nextLevelName": "白银等级", "pendingOptimizeDesc": "5项待优化",
            "rankDescPrefix": "本商圈排名", "rankDescSuffix": "199/242",
            "chotDataList": [
                {"dataName": "本房热度", "dataValue": "125", "fluctuateVal": "20%",
                 "isPositive": 1},
            ],
            "optimizeSuggestionList": [
                {"optimizeItemName": "房间整洁度-实勘图AI评估",
                 "suggestionDesc": "清理客厅及卧室的垃圾与杂物"},
            ],
        },
    }
    score = _parse_hqi_score(body, "106128274229")

    assert score is not None
    assert score.total_score == "38"
    assert score.level is None
    assert score.next_level == "白银等级"
    assert score.rank_text == "本商圈排名199/242"
    assert score.pending_optimize == "5项待优化"
    assert score.heat_items[0].name == "本房热度"
    assert score.heat_items[0].value == "125"
    assert score.heat_items[0].fluctuate == "20%"
    assert score.heat_items[0].positive is True
    assert score.suggestions[0].item == "房间整洁度-实勘图AI评估"


def test_parse_hqi_score_empty_data_means_no_score_record() -> None:
    # Verified live: 106128807039 returns data:{} — a valid "no score" answer.
    assert _parse_hqi_score({"code": 100000, "data": {}}, "106128807039") is None


def test_parse_maintain_info_maps_modules_and_remark() -> None:
    # body shape from the live getMaintainInfo capture (106128274229)
    body = {
        "code": 100000,
        "data": {
            "delCode": "106128274229",
            "importantModules": [{
                "completenessRate": "完备率：9/9(100%)",
                "fields": [
                    {"fieldName": "可入住时间", "displayValue": "随时入住", "complete": True},
                    {"fieldName": "家具",
                     "displayValue": "床/衣柜/桌椅/单人床/双人床/沙发/蹲便", "complete": True},
                    {"fieldName": "租期", "displayValue": "2年以内", "complete": True},
                ],
            }],
            "otherModules": [{
                "completenessRate": "完备率：1/4(25%)",
                "fields": [
                    {"fieldName": "设施", "displayValue": "无", "complete": True},
                    {"fieldName": "房屋格局是否有变动", "displayValue": "--", "complete": False},
                ],
            }],
            "remark": "实勘图被买卖覆盖目前很干净。钥匙在门店或者门口小中介拿 给我打电话",
            "allFieldMaintainRate": 75,
            "importantFieldMaintainRate": 100,
            "ownerLowestPrice": 2900,
        },
    }
    info = _parse_maintain_info(body, "106128274229")

    assert info.listing_id == "106128274229"
    assert len(info.modules) == 2
    important, other = info.modules
    assert important.rate_text == "完备率：9/9(100%)"
    assert [f.name for f in important.fields] == ["可入住时间", "家具", "租期"]
    assert important.fields[0].display_value == "随时入住"
    assert important.fields[0].complete is True
    assert other.fields[1].display_value == "--"
    assert other.fields[1].complete is False
    assert info.remark == "实勘图被买卖覆盖目前很干净。钥匙在门店或者门口小中介拿 给我打电话"
    assert info.all_field_rate == 75
    assert info.important_rate == 100
    assert info.owner_lowest_price == "2900"


def test_parse_maintain_info_raises_when_data_empty() -> None:
    # Probed 2026-08-09: unknown id -> code=100001 房源编码错误 (invalid input).
    with pytest.raises(UpstreamInvalidInputError):
        _parse_maintain_info({"code": 100000, "data": {}}, "999999999")


def test_parse_follow_records_maps_records_and_skips_empty_content() -> None:
    body = {
        "code": 100000,
        "data": {
            "totalCount": 2,
            "result": [
                {
                    "followUpContent": "真实在租房东，租带卖，附近最有性价比的电梯套三",
                    "followTypeStr": "普通跟进", "creatorName": "万世平",
                    "roleTypeStr": "维护人", "createTime": 1785403336000,
                    "followLabel": ["真实在租", "真实在租"], "followLabelCode": "IN_RENT",
                    "remarks": "业主脾气好", "onTop": True,
                    "onTopTime": 1785403336000,
                },
                {"followUpContent": "", "followTypeStr": "普通跟进"},
            ],
        },
    }
    records = _parse_follow_records(body)

    assert len(records) == 1
    assert records[0].content == "真实在租房东，租带卖，附近最有性价比的电梯套三"
    assert records[0].follow_type == "普通跟进"
    assert records[0].creator_name == "万世平"
    assert records[0].role == "维护人"
    assert records[0].labels == ("真实在租",)  # deduplicated
    assert records[0].label_code == "IN_RENT"
    assert records[0].created_at is not None
    assert records[0].created_at.year == 2026
    assert records[0].remarks == "业主脾气好"
    assert records[0].on_top is True
    assert records[0].on_top_time is not None
    assert records[0].on_top_time == records[0].created_at


def test_parse_follow_records_none_result_means_no_follows() -> None:
    # Verified live: 106128807039 returns totalCount=0 with result=None.
    assert _parse_follow_records({"code": 100000, "data": {"result": None}}) == ()
    assert _parse_follow_records({"code": 100000, "data": {}}) == ()


def test_get_rental_listing_house_info_aggregates_five_records() -> None:
    session = CapturingSession()
    session.enqueue("rental_listing.get_hdic_info", 200, {
        "code": 100000, "data": {"resblockName": "成发紫东阳光", "tiHuRatio": "2梯5户"},
    })
    session.enqueue("rental_listing.get_house_label", 200, {
        "code": 100000, "data": ["电梯房", "VR房"],
    })
    session.enqueue("rental_listing.get_hqi_tab", 200, {
        "code": 100000, "data": {"totalScoreValue": "38"},
    })
    session.enqueue("rental_listing.get_maintain_info", 200, {
        "code": 100000, "data": {
            "delCode": "106128274229",
            "importantModules": [{
                "completenessRate": "完备率：9/9(100%)",
                "fields": [{"fieldName": "装修情况", "displayValue": "精装", "complete": True}],
            }],
            "remark": "钥匙在门店",
            "allFieldMaintainRate": 75,
            "importantFieldMaintainRate": 100,
            "ownerLowestPrice": 2900,
        },
    })
    session.enqueue("rental_listing.get_follow", 200, {
        "code": 100000, "data": {
            "totalCount": 1, "result": [{
                "followUpContent": "真实在租房东，租带卖，附近最有性价比的电梯套三",
                "followTypeStr": "普通跟进", "creatorName": "万世平",
                "roleTypeStr": "维护人", "createTime": 1785403336000,
                "followLabel": ["真实在租"], "followLabelCode": "IN_RENT",
                "remarks": "业主脾气好", "onTop": True, "onTopTime": 1785403336000,
            }],
        },
    })
    client = KecomCrmClient(session)

    info = client.get_rental_listing_house_info("106128274229")

    assert [call.route for call in session.calls] == [
        "rental_listing.get_hdic_info",
        "rental_listing.get_house_label",
        "rental_listing.get_hqi_tab",
        "rental_listing.get_maintain_info",
        "rental_listing.get_follow",
    ]
    assert session.calls[0].query == {"delCode": "106128274229"}
    assert session.calls[1].query == {"delCode": "106128274229"}
    assert session.calls[2].query == {"delCode": "106128274229", "isApp": "false"}
    assert session.calls[3].query == {"delCode": "106128274229"}
    assert session.calls[4].query == {"delCode": "106128274229", "pageSize": "100"}
    assert info.listing_id == "106128274229"
    assert info.labels == ("电梯房", "VR房")
    assert info.property_info is not None and info.property_info.community == "成发紫东阳光"
    assert info.hqi is not None and info.hqi.total_score == "38"
    assert info.maintain is not None
    assert info.maintain.modules[0].rate_text == "完备率：9/9(100%)"
    assert info.maintain.modules[0].fields[0].name == "装修情况"
    assert info.maintain.modules[0].fields[0].display_value == "精装"
    assert info.maintain.remark == "钥匙在门店"
    assert info.maintain.all_field_rate == 75
    assert info.maintain.important_rate == 100
    assert info.maintain.owner_lowest_price == "2900"
    assert len(info.follows) == 1
    assert info.follows[0].content == "真实在租房东，租带卖，附近最有性价比的电梯套三"
    assert info.follows[0].follow_type == "普通跟进"
    assert info.follows[0].creator_name == "万世平"
    assert info.follows[0].role == "维护人"
    assert info.follows[0].labels == ("真实在租",)
    assert info.follows[0].label_code == "IN_RENT"
    assert info.follows[0].remarks == "业主脾气好"
    assert info.follows[0].on_top is True


def test_get_rental_listing_house_info_allows_missing_hqi_and_empty_follows() -> None:
    session = CapturingSession()
    session.enqueue("rental_listing.get_hdic_info", 200, {
        "code": 100000, "data": {"resblockName": "双桥路南一街"},
    })
    session.enqueue("rental_listing.get_house_label", 200, {
        "code": 100000, "data": ["VR房"],
    })
    session.enqueue("rental_listing.get_hqi_tab", 200, {
        "code": 100000, "data": {},
    })
    # 106128807039 verified live: getMaintainInfo remark=None, detailFollow
    # totalCount=0 with result=None — both are valid, not errors.
    session.enqueue("rental_listing.get_maintain_info", 200, {
        "code": 100000, "data": {
            "delCode": "106128807039",
            "importantModules": [], "otherModules": [],
            "remark": None, "allFieldMaintainRate": 0,
            "importantFieldMaintainRate": 0, "ownerLowestPrice": None,
        },
    })
    session.enqueue("rental_listing.get_follow", 200, {
        "code": 100000, "data": {"totalCount": 0, "result": None},
    })
    client = KecomCrmClient(session)

    info = client.get_rental_listing_house_info("106128807039")

    assert info.hqi is None
    assert info.property_info is not None
    assert info.labels == ("VR房",)
    assert info.maintain is not None and info.maintain.remark is None
    assert info.maintain.modules == ()
    assert info.follows == ()


def test_get_rental_listing_prospect_through_session_boundary() -> None:
    session = CapturingSession()
    session.enqueue(
        "rental_listing.detail_prospect", 200,
        {
            "code": 100000, "msg": "加载成功",
            "data": {
                "canEditProspect": True,
                "houseFrameImageResp": {"imageUrl": "https://img.ke.com/huxing.png"},
                "houseProspectImageList": [
                    {"prospectPicUrl": "https://img.ke.com/real-1.jpg",
                     "roomName": "卧室", "imageType": "REAL",
                     "uploadUserName": "李四", "createTime": 1750000000000},
                ],
            },
        },
    )
    client = KecomCrmClient(session)

    prospect = client.get_rental_listing_prospect("106128762229")

    assert session.calls[0].route == "rental_listing.detail_prospect"
    assert session.calls[0].query == {"delCode": "106128762229"}
    assert prospect.has_survey_photo is True
    assert prospect.photos[0].upload_user == "李四"


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
                "orgName": "德佑-承嘉-水碾河店A组", "delResourceSub": "呼叫中心",
                "floorDesc": "高楼层", "totalFloor": 31, "alreadyCreateDays": 42,
                "followTotal": 3, "followNum7Days": 1,
                "showingTotal": 0, "showingNum7Days": 0,
                "keUrl": "https://m.ke.com/chuzu/cd/zufang/X1.html",
                "lianJiaUrl": "https://m.lianjia.com/chuzu/cd/zufang/X1.html",
                "haveKey": True, "delStatusString": "有效", "houseId": 25853701,
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
    # Detail-only fields from the same detailHead record.
    assert listing.maintain_org == "德佑-承嘉-水碾河店A组"
    assert listing.source == "呼叫中心"
    assert listing.floor_desc == "高楼层"
    assert listing.total_floors == 31
    assert listing.listed_days == 42
    assert listing.house_grade == "B"
    assert listing.follow_total == 3
    assert listing.follow_last_7d == 1
    assert listing.showing_total == 0
    assert listing.showing_last_7d == 0
    assert listing.external_url_ke == "https://m.ke.com/chuzu/cd/zufang/X1.html"
    assert listing.external_url_lianjia == "https://m.lianjia.com/chuzu/cd/zufang/X1.html"
    assert listing.has_key is True
    assert listing.del_status_text == "有效"
    assert listing.house_id == "25853701"


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
