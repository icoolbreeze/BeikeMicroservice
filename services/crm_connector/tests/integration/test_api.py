from datetime import UTC, datetime

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
        self.ready = True
        self.expires_at: datetime | None = None

    def status(self) -> ProviderStatus:
        if not self.ready:
            return ProviderStatus(ConnectionState.AUTH_REQUIRED, "stub unauthenticated")
        return ProviderStatus(
            ConnectionState.READY, "stub ready", expires_at=self.expires_at
        )

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


def test_health_reports_credential_validity(tmp_path) -> None:
    session = StubSession()
    session.expires_at = datetime(2026, 9, 1, tzinfo=UTC)
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["service"] == "crm_connector"
    assert body["connection_state"] == "ready"
    assert body["credential_valid"] is True
    assert datetime.fromisoformat(body["credential_expires_at"]) == session.expires_at
    # The connection status endpoint surfaces the same validity deadline.
    status = client.get("/api/v1/connection/status")
    assert status.status_code == 200
    assert (
        datetime.fromisoformat(status.json()["credential_expires_at"])
        == session.expires_at
    )


def test_health_reports_absent_credential(tmp_path) -> None:
    session = StubSession()
    session.ready = False
    _app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["connection_state"] == "auth_required"
    assert body["credential_valid"] is False
    assert body["credential_expires_at"] is None


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
        "del_type": None,
        # detail-only fields are absent on search rows
        "maintain_org": None, "source": None, "floor_desc": None,
        "total_floors": None, "listed_days": None, "house_grade": None,
        "follow_total": None, "follow_last_7d": None,
        "showing_total": None, "showing_last_7d": None,
        "external_url_ke": None, "external_url_lianjia": None,
        "has_key": None, "del_status_text": None, "house_id": None,
        "title_image_url": None, "floor_plan_image_url": None,
    }
    # The request reached SessionProvider with the documented upstream params.
    assert len(session.calls) == 1
    request = session.calls[0]
    assert request.route == "rental_listing.search"
    assert request.query["communityKeyword"] == "万象城"
    assert request.query["sceneCode"] == "puzu_mix_list_pc"
    assert request.query["relationRange"] == 1
    assert request.query["clientOsType"] == 3


def test_search_accepts_unrestricted_rental_scope(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {
            "code": 100000,
            "data": {"result": [], "totalCount": 0, "totalPage": 1},
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/rental/search",
        json={"scope": "all", "page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    assert session.calls[0].query["relationRange"] == 0


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
                "orgName": "德佑-承嘉-水碾河店A组", "delResourceSub": "呼叫中心",
                "floorDesc": "高楼层", "totalFloor": 18, "alreadyCreateDays": 42,
                "houseGrade": "B", "followTotal": 5, "followNum7Days": 1,
                "showingTotal": 2, "showingNum7Days": 0,
                "keUrl": "https://m.ke.com/chuzu/cd/zufang/X1.html",
                "lianJiaUrl": "https://m.lianjia.com/chuzu/cd/zufang/X1.html",
                "haveKey": True, "delStatusString": "有效", "houseId": 25853701,
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
    assert listing["maintain_org"] == "德佑-承嘉-水碾河店A组"
    assert listing["source"] == "呼叫中心"
    assert listing["floor_desc"] == "高楼层"
    assert listing["total_floors"] == 18
    assert listing["listed_days"] == 42
    assert listing["house_grade"] == "B"
    assert listing["follow_total"] == 5
    assert listing["follow_last_7d"] == 1
    assert listing["showing_total"] == 2
    assert listing["showing_last_7d"] == 0
    assert listing["external_url_ke"] == "https://m.ke.com/chuzu/cd/zufang/X1.html"
    assert listing["external_url_lianjia"] == "https://m.lianjia.com/chuzu/cd/zufang/X1.html"
    assert listing["has_key"] is True
    assert listing["del_status_text"] == "有效"
    assert listing["house_id"] == "25853701"
    assert len(session.calls) == 1
    assert session.calls[0].route == "rental_listing.get_detail"
    assert session.calls[0].query == {"delCode": "RC-42"}


def test_get_prospect_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.detail_prospect", 200,
        {
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
                     "uploadUserName": None, "createTime": None},
                ],
            },
        },
    )
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/RC-42/prospect")

    assert response.status_code == 200
    prospect = response.json()
    assert prospect["listing_id"] == "RC-42"
    assert prospect["has_survey_photo"] is True
    assert prospect["floor_plan_url"] == "https://img.ke.com/huxing.png"
    assert prospect["can_edit"] is False
    assert len(prospect["photos"]) == 2
    assert prospect["photos"][0]["image_type"] == "REAL"
    assert prospect["photos"][0]["room_name"] == "客厅"
    assert prospect["photos"][1]["created_at"] is None
    assert len(session.calls) == 1
    assert session.calls[0].route == "rental_listing.detail_prospect"
    assert session.calls[0].query == {"delCode": "RC-42"}


def test_get_house_info_flows_through_full_app_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue("rental_listing.get_hdic_info", 200, {
        "code": 100000,
        "data": {"resblockName": "成发紫东阳光", "districtName": "成华",
                 "buildTypeName": "塔楼", "buildingYear": 2015,
                 "tiHuRatio": "2梯5户", "tenementFeeStr": "2.15",
                 "waterTypeName": "民水", "electricTypeName": "民电",
                 "gasStr": "有", "gasFeeStr": "2.03", "hotWaterStr": "有",
                 "heatingFeeStr": "", "carRatio": "1:0.48", "parkingFee": "350",
                 "carUpCntStr": "无", "carDownCntStr": "361",
                 "hauntedDesc": "高压线", "kindergarten": "成华区第三幼儿园",
                 "greenRate": 26},
    })
    session.enqueue("rental_listing.get_house_label", 200, {
        "code": 100000, "data": ["电梯房", "VR房", "钥", "学区房"],
    })
    session.enqueue("rental_listing.get_hqi_tab", 200, {
        "code": 100000,
        "data": {"totalScoreValue": "38", "rankDescPrefix": "本商圈排名",
                 "rankDescSuffix": "199/242",
                 "chotDataList": [{"dataName": "本房热度", "dataValue": "125"}],
                 "optimizeSuggestionList": [
                     {"optimizeItemName": "房间整洁度-实勘图AI评估",
                      "suggestionDesc": "清理客厅及卧室的垃圾"}]},
    })
    session.enqueue("rental_listing.get_maintain_info", 200, {
        "code": 100000,
        "data": {"delCode": "RC-42",
                 "importantModules": [{
                     "completenessRate": "完备率：9/9(100%)",
                     "fields": [{"fieldName": "装修情况", "displayValue": "精装",
                                 "complete": True},
                                {"fieldName": "租期", "displayValue": "2年以内",
                                 "complete": True}]}],
                 "remark": "钥匙在门店",
                 "allFieldMaintainRate": 75,
                 "importantFieldMaintainRate": 100,
                 "ownerLowestPrice": 2900},
    })
    session.enqueue("rental_listing.get_follow", 200, {
        "code": 100000,
        "data": {"totalCount": 1, "result": [{
            "followUpContent": "真实在租房东，租带卖，附近最有性价比的电梯套三",
            "followTypeStr": "普通跟进", "creatorName": "万世平",
            "roleTypeStr": "维护人", "createTime": 1785403336000,
            "followLabel": ["真实在租"], "followLabelCode": "IN_RENT",
            "remarks": "业主脾气好", "onTop": True, "onTopTime": 1785403336000}]},
    })
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/RC-42/house-info")

    assert response.status_code == 200
    info = response.json()
    assert info["listing_id"] == "RC-42"
    assert info["labels"] == ["电梯房", "VR房", "钥", "学区房"]
    assert info["property_info"]["community"] == "成发紫东阳光"
    assert info["property_info"]["ti_hu_ratio"] == "2梯5户"
    assert info["property_info"]["building_year"] == 2015
    assert info["property_info"]["green_rate"] == 26.0
    assert info["property_info"]["parking_fee"] == "350"
    assert info["property_info"]["gas_fee"] == "2.03"
    assert info["property_info"]["hot_water"] == "有"
    assert info["property_info"]["heating_fee"] is None  # 空串 -> None
    assert info["property_info"]["parking_above_ground"] == "无"
    assert info["property_info"]["parking_underground"] == "361"
    assert info["property_info"]["haunted_desc"] == "高压线"
    assert info["property_info"]["kindergarten"] == "成华区第三幼儿园"
    assert info["hqi"]["total_score"] == "38"
    assert info["hqi"]["rank_text"] == "本商圈排名199/242"
    assert info["hqi"]["heat_items"][0]["name"] == "本房热度"
    assert info["hqi"]["suggestions"][0]["item"] == "房间整洁度-实勘图AI评估"
    assert info["maintain"]["remark"] == "钥匙在门店"
    assert info["maintain"]["all_field_rate"] == 75
    assert info["maintain"]["owner_lowest_price"] == "2900"
    assert info["maintain"]["modules"][0]["fields"][0]["display_value"] == "精装"
    assert info["follows"][0]["content"] == "真实在租房东，租带卖，附近最有性价比的电梯套三"
    assert info["follows"][0]["creator_name"] == "万世平"
    assert info["follows"][0]["label_code"] == "IN_RENT"
    assert info["follows"][0]["remarks"] == "业主脾气好"
    assert info["follows"][0]["on_top"] is True
    assert [call.route for call in session.calls] == [
        "rental_listing.get_hdic_info",
        "rental_listing.get_house_label",
        "rental_listing.get_hqi_tab",
        "rental_listing.get_maintain_info",
        "rental_listing.get_follow",
    ]
    assert session.calls[0].query == {"delCode": "RC-42"}
    assert session.calls[1].query == {"delCode": "RC-42"}
    assert session.calls[2].query == {"delCode": "RC-42", "isApp": "false"}
    assert session.calls[3].query == {"delCode": "RC-42"}
    assert session.calls[4].query == {"delCode": "RC-42", "pageSize": "100"}


def test_get_house_info_missing_hqi_returns_null_without_error(tmp_path) -> None:
    session = StubSession()
    session.enqueue("rental_listing.get_hdic_info", 200, {
        "code": 100000, "data": {"resblockName": "双桥路南一街"},
    })
    session.enqueue("rental_listing.get_house_label", 200, {
        "code": 100000, "data": ["VR房"],
    })
    session.enqueue("rental_listing.get_hqi_tab", 200, {
        "code": 100000, "data": {},
    })
    session.enqueue("rental_listing.get_maintain_info", 200, {
        "code": 100000, "data": {"delCode": "RC-42",
                                 "importantModules": [], "otherModules": [],
                                 "remark": None, "allFieldMaintainRate": 0,
                                 "importantFieldMaintainRate": 0,
                                 "ownerLowestPrice": None},
    })
    session.enqueue("rental_listing.get_follow", 200, {
        "code": 100000, "data": {"totalCount": 0, "result": None},
    })
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/RC-42/house-info")

    assert response.status_code == 200
    info = response.json()
    assert info["hqi"] is None
    assert info["labels"] == ["VR房"]
    assert info["maintain"]["modules"] == []
    assert info["maintain"]["remark"] is None
    assert info["follows"] == []


def test_get_prospect_empty_photos_is_valid_not_surveyed_answer(tmp_path) -> None:
    # A real 普租 house without survey photos returns an empty image list,
    # not an error — the endpoint must surface that as has_survey_photo=false.
    session = StubSession()
    session.enqueue(
        "rental_listing.detail_prospect", 200,
        {"code": 100000, "msg": "加载成功",
         "data": {"canEditProspect": False, "houseProspectImageList": []}},
    )
    app, client = _wired_app(session, tmp_path)

    response = client.get("/api/v1/listings/rental/106128814453/prospect")

    assert response.status_code == 200
    prospect = response.json()
    assert prospect["has_survey_photo"] is False
    assert prospect["photos"] == []
    assert prospect["floor_plan_url"] is None


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
    assert body["community_ids"] == ["rb-near"]
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


def test_sale_map_nearby_resolves_location_then_reuses_community_ids(tmp_path) -> None:
    """买卖 nearby: sale suggest -> community bubbles -> radius filter -> list search."""
    session = StubSession()
    session.enqueue(
        "sale_map.suggest", 200,
        {"code": 1, "data": [{
            "id": 1611048089809, "text": "华润广场(成华)", "alias": "万象城",
            "type": "community", "latitude": "30.655076", "longitude": "104.124096",
        }]},
    )
    session.enqueue(
        "sale_map.bubbles", 200,
        {"code": 1, "data": {"list": {
            "1611044656331": {"id": 1611044656331, "name": "尖东旺座一期", "count": 7,
                              "latitude": "30.6495", "longitude": "104.1195"},
            "1611059597918": {"id": 1611059597918, "name": "银通苑", "count": 13,
                              "latitude": "30.6626", "longitude": "104.1249"},
        }}},
    )
    session.enqueue(
        "sale_listing.search", 200,
        {"code": 1, "data": {
            "totalCount": 2, "currentPage": 1, "totalPage": 1,
            "list": [{
                "houseDelCode": "S-map-1", "communityName": "尖东旺座一期",
                "unitType": "2-1-1-1", "areaSize": 47.78, "totalPrice": 650000.0,
                "totalPriceStr": "65万", "floorType": "高", "totalFloor": "7",
            }],
        }},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/sale/map/nearby",
        json={
            "location": "万象城", "radius_meters": 1000,
            "total_price_wan": {"min": 50, "max": 70}, "rooms": [2],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["center"]["text"] == "华润广场(成华)"
    assert body["matched_community_count"] == 2
    assert body["community_ids"] == ["1611044656331", "1611059597918"]
    assert body["community_ids_truncated"] is False
    assert body["approximation"] == "community_centroid"
    assert body["result"]["items"][0]["listing_id"] == "S-map-1"
    assert [call.route for call in session.calls] == [
        "sale_map.suggest", "sale_map.bubbles", "sale_listing.search",
    ]
    assert session.calls[1].query["group_type"] == "community"
    assert session.calls[2].query["multi_community_id"] == (
        "1611044656331,1611059597918"
    )
    assert session.calls[2].query["price"] == "50,70"
    assert session.calls[2].query["room"] == "2,2"


def test_sale_map_nearby_marks_communities_truncated(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "sale_map.suggest", 200,
        {"code": 1, "data": [{
            "id": 1611048089809, "text": "华润广场(成华)", "alias": "万象城",
            "type": "community", "latitude": "30.655076", "longitude": "104.124096",
        }]},
    )
    bubbles = {
        str(index): {
            "id": index,
            "name": f"测试小区{index}",
            "count": 1,
            "latitude": "30.655",
            "longitude": "104.124",
        }
        for index in range(101)
    }
    session.enqueue("sale_map.bubbles", 200, {"code": 1, "data": {"list": bubbles}})
    session.enqueue(
        "sale_listing.search", 200,
        {"code": 1, "data": {"totalCount": 0, "currentPage": 1, "totalPage": 0, "list": []}},
    )
    _app, client = _wired_app(session, tmp_path)

    response = client.post(
        "/api/v1/listings/sale/map/nearby",
        json={"location": "万象城", "radius_meters": 1000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched_community_count"] == 101
    assert len(body["community_ids"]) == 100
    assert body["community_ids_truncated"] is True
    assert len(session.calls[2].query["multi_community_id"].split(",")) == 100

