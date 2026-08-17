from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from app.bootstrap import build_service
from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.providers.session_provider import AuthorizedRequest, UpstreamResponse
from app.infrastructure.settings import Settings
from app.mcp.server import build_mcp_server
from app.mcp.tools import tool_definitions

SERVICE_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["asyncio"])
def anyio_backend(request) -> str:
    """Run all async tests on the asyncio backend only."""
    return request.param


class StubSession:
    """READY session that replays canned UpstreamResponses for KecomCrmClient."""

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


class AuthRequiredSession(StubSession):
    """Session that reports AUTH_REQUIRED for every gated call."""

    def status(self) -> ProviderStatus:
        return ProviderStatus(ConnectionState.AUTH_REQUIRED, "no credential")


def _wired_server(session: StubSession, tmp_path, *, rate_limit: int = 30):
    """Build the real profile service, then swap its session for a stub."""
    settings = Settings(
        upstream_profile="kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        bound_employee_principal="100000003",
        mcp_rate_limit_per_min=rate_limit,
    )
    service = build_service(settings)
    service._session_provider = session  # type: ignore[attr-defined]
    service._crm_client._session = session  # type: ignore[attr-defined]
    return build_mcp_server(service, settings)


def _text(result) -> dict[str, Any]:
    body = json.loads(_first_text(result))
    assert isinstance(body, dict)
    return body


def _first_text(result) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


@pytest.mark.anyio(backend="asyncio")
async def test_tool_discovery_exposes_map_tools_with_read_only_hint(tmp_path) -> None:
    server = _wired_server(StubSession(), tmp_path)
    async with Client(server) as client:
        tools = await client.list_tools()
        assert sorted(tool.name for tool in tools.tools) == [
            "crm_connection_status",
            "crm_whoami",
            "rental_listing_filter_options",
            "rental_listing_get_detail",
            "rental_listing_get_house_info",
            "rental_listing_get_prospect",
            "rental_listing_search",
            "rental_map_nearby_search",
            "rental_map_suggest",
            "sale_community_suggest",
            "sale_listing_filter_options",
            "sale_listing_get_detail",
            "sale_listing_get_detail_head",
            "sale_listing_get_follows",
            "sale_listing_get_maintain_info",
            "sale_listing_search",
            "sale_map_nearby_search",
            "sale_map_suggest",
            "tuoguan_listing_get_deals",
            "tuoguan_listing_get_detail",
            "tuoguan_listing_search",
        ]
        assert all(
            tool.annotations is not None and tool.annotations.read_only_hint
            for tool in tools.tools
        )


@pytest.mark.anyio(backend="asyncio")
async def test_connection_status_tool_is_local_and_not_rate_limited(tmp_path) -> None:
    session = StubSession()
    server = _wired_server(session, tmp_path, rate_limit=1)
    async with Client(server) as client:
        for _ in range(3):  # status must pass even past the quota
            result = await client.call_tool("crm_connection_status", {})
            assert result.is_error is False
            body = _text(result)
            assert body["state"] == "ready"
            assert body["mcp_transport"] == "stdio"


@pytest.mark.anyio(backend="asyncio")
async def test_whoami_flows_through_real_pipeline(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "identity.me", 200, {"code": 100000, "data": {"ucid": "100000003", "name": "张三"}}
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool("crm_whoami", {})
        assert result.is_error is False
        assert _text(result) == {"employee_principal": "100000003", "display_name": "张三"}
        assert session.calls[0].route == "identity.me"


@pytest.mark.anyio(backend="asyncio")
async def test_search_flows_through_real_pipeline(tmp_path) -> None:
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
                ],
                "totalCount": 1, "totalPage": 1,
            },
        },
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_search",
            {"input": {"community_keyword": "万象城", "page": 1, "page_size": 20}},
        )
        assert result.is_error is False
        body = _text(result)
        assert body["page"] == 1 and body["has_more"] is False
        assert [item["community"] for item in body["items"]] == ["万象城一期"]
        assert body["items"][0]["monthly_rent_yuan"] == 3500.0
        assert session.calls[0].route == "rental_listing.search"
        assert session.calls[0].query["communityKeyword"] == "万象城"


@pytest.mark.anyio(backend="asyncio")
async def test_nearby_map_tool_follows_draw_circle_pipeline(tmp_path) -> None:
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
        {"code": 0, "data": {"bubbleList": [{
            "id": "rb-1", "name": "万象城一期", "latitude": 30.651, "longitude": 104.101,
        }]}},
    )
    session.enqueue(
        "rental_map.search_circle", 200,
        {"code": 0, "data": {"list": [{
            "delCode": "RC-map-1", "title": "万象城附近套二", "desc": "2室1厅",
        }], "total": 1}},
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_map_nearby_search",
            {"input": {
                "location": "万象城", "radius_meters": 1000,
                "price_min_yuan": 1800, "price_max_yuan": 2200, "rooms": [2],
                "rental_modes": ["whole_rent"],
            }},
        )
        assert result.is_error is False
        body = _text(result)
        assert body["matched_community_count"] == 1
        assert body["community_ids"] == ["rb-1"]
        assert body["community_ids_truncated"] is False
        assert body["result"]["items"][0]["listing_id"] == "RC-map-1"
        assert [call.route for call in session.calls] == [
            "rental_map.suggest", "rental_map.bubbles", "rental_map.search_circle",
        ]
        assert session.calls[2].query["condition"] == "obrp1800oerp2200l2rt001"


@pytest.mark.anyio(backend="asyncio")
async def test_detail_flows_through_real_pipeline(tmp_path) -> None:
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
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_get_detail",
            {"input": {"listing_id": "RC-42"}},
        )
        assert result.is_error is False
        listing = _text(result)
        # listing_id comes from the upstream's authoritative delCode
        assert listing["listing_id"] == "106128814453"
        assert listing["community"] == "双桥路南一街"
        assert listing["layout"] == "3室2厅2卫"
        assert session.calls[0].route == "rental_listing.get_detail"
        assert session.calls[0].query == {"delCode": "RC-42"}


@pytest.mark.anyio(backend="asyncio")
async def test_get_prospect_tool_serves_survey_record(tmp_path) -> None:
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
                ],
            },
        },
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_get_prospect",
            {"input": {"listing_id": "RC-42"}},
        )
        assert result.is_error is False
        prospect = _text(result)
        assert prospect["listing_id"] == "RC-42"
        assert prospect["has_survey_photo"] is True
        assert prospect["photos"][0]["image_type"] == "REAL"
        assert session.calls[0].route == "rental_listing.detail_prospect"
        assert session.calls[0].query == {"delCode": "RC-42"}


@pytest.mark.anyio(backend="asyncio")
async def test_get_prospect_tool_reports_not_surveyed_without_error(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.detail_prospect", 200,
        {"code": 100000, "msg": "加载成功",
         "data": {"canEditProspect": False, "houseProspectImageList": []}},
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_get_prospect",
            {"input": {"listing_id": "106128814453"}},
        )
        assert result.is_error is False
        prospect = _text(result)
        assert prospect["has_survey_photo"] is False
        assert prospect["photos"] == []


@pytest.mark.anyio(backend="asyncio")
async def test_tuoguan_get_detail_tool_serves_trusteeship_head(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "trusteeship.get_detail", 200,
        {
            "code": 100000, "msg": "提交成功",
            "data": {
                "houseHeadInfo": {
                    "houseDelCode": 106126181022,
                    "resblockName": "环球汇广场",
                    "guidePrice": 2000,
                    "houseProspectList": [
                        {"name": "01间-主卧", "url": "/lease-image/house/ec59fd2505.jpeg",
                         "primaryFlag": 1, "createTime": "2026-08-14 14:32:08"},
                    ],
                    "houseTypeList": [{"name": "户型图", "url": "/hdic-frame/x.jpg"}],
                    "tagList": [{"desc": "贝壳省心租·自营"}],
                    "managerInfo": {"userName": "陈蒋", "roleName": "房管人",
                                    "orgName": "川师区", "phone": "15608003828"},
                },
                "dealInfo": {"dealDetails": [], "dealInfo": {}},
            },
        },
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "tuoguan_listing_get_detail",
            {"input": {"cell_code": "10612612882101"}},
        )
        assert result.is_error is False
        detail = _text(result)
        assert detail["resblock_name"] == "环球汇广场"
        assert detail["house_del_code"] == "106126181022"
        assert detail["prospects"][0]["name"] == "01间-主卧"
        assert detail["prospects"][0]["url"] == (
            "https://img.ljcdn.com/lease-image/house/ec59fd2505.jpeg"
        )
        assert detail["manager"]["user_name"] == "陈蒋"
        assert session.calls[0].route == "trusteeship.get_detail"
        assert session.calls[0].query == {"cellCode": "10612612882101"}


@pytest.mark.anyio(backend="asyncio")
async def test_tuoguan_get_deals_tool_paginates(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "trusteeship.get_deals", 200,
        {
            "code": 100000, "msg": "加载成功",
            "data": {
                "hasMore": False, "totalCount": 1,
                "result": [{"dealPrice": "2100元/月", "dealTime": "2026.08.10",
                            "desc": "41.87m²｜西南｜7/29｜电梯", "layout": None,
                            "onRentTime": "2026.07.05", "prospect": None}],
            },
        },
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "tuoguan_listing_get_deals",
            {"input": {"cell_code": "10612612882101", "page": 2, "page_size": 10}},
        )
        assert result.is_error is False
        page = _text(result)
        assert page["total"] == 1 and page["has_more"] is False
        assert page["items"][0]["deal_price"] == "2100元/月"
        assert session.calls[0].route == "trusteeship.get_deals"
        assert session.calls[0].query == {
            "cellCode": "10612612882101", "pageIndex": 2, "pageSize": 10,
        }


@pytest.mark.anyio(backend="asyncio")
async def test_tuoguan_listing_search_returns_working_cell_codes(tmp_path) -> None:
    # Fixture mirrors the live waitingrent capture (2026-08-15): rows carry
    # bizCode (cell_code), resblockName, guidePrice, cellArea, floorNum.
    session = StubSession()
    session.enqueue(
        "trusteeship.search_listings", 200,
        {
            "code": 100000, "msg": "加载成功",
            "data": {
                "total": 13418, "more": False,
                "list": [{
                    "bizCode": 10611958598901, "resblockName": "阳光365",
                    "bizCircleName": "盐市口", "buildingName": "1号楼",
                    "houseName": "1735", "bedroomAmount": 1, "bathroomAmount": 1,
                    "cellArea": 42.81, "floorNum": 17, "guidePrice": 2100,
                    "canLookTime": "2026-07-16 17:25:57",
                }],
            },
        },
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "tuoguan_listing_search",
            {"input": {"page": 1, "page_size": 20}},
        )
        assert result.is_error is False
        page = _text(result)
        assert page["total"] == 13418
        assert page["has_more"] is True  # 1*20 < 13418
        row = page["items"][0]
        assert row["cell_code"] == "10611958598901"
        assert row["community"] == "阳光365"
        assert row["layout_text"] == "1室1卫"
        assert row["guide_price_yuan"] == 2100
        call = session.calls[0]
        assert call.route == "trusteeship.search_listings"
        assert call.body["searchStatus"] == 1
        assert call.body["pageSize"] == 20
        assert "delCode" not in call.body


@pytest.mark.anyio(backend="asyncio")
async def test_tuoguan_listing_search_cell_code_lookup_sends_both_params(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "trusteeship.search_listings", 200,
        {"code": 100000, "msg": "加载成功", "data": {"total": 1, "more": False, "list": []}},
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "tuoguan_listing_search",
            {"input": {"cell_code": "10611958598901"}},
        )
        assert result.is_error is False
        call = session.calls[0]
        assert call.body["delCode"] == "10611958598901"
        assert call.body["outHouseCode"] == "10611958598901"


@pytest.mark.anyio(backend="asyncio")
async def test_get_house_info_tool_aggregates_detail_info(tmp_path) -> None:
    session = StubSession()
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
            "delCode": "RC-42",
            "importantModules": [{
                "completenessRate": "完备率：9/9(100%)",
                "fields": [{"fieldName": "装修情况", "displayValue": "精装", "complete": True}],
            }],
            "remark": "钥匙在门店", "allFieldMaintainRate": 75,
            "importantFieldMaintainRate": 100, "ownerLowestPrice": 2900,
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
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_get_house_info",
            {"input": {"listing_id": "RC-42"}},
        )
        assert result.is_error is False
        info = _text(result)
        assert info["listing_id"] == "RC-42"
        assert info["labels"] == ["电梯房", "VR房"]
        assert info["property_info"]["ti_hu_ratio"] == "2梯5户"
        assert info["hqi"]["total_score"] == "38"
        assert info["maintain"]["modules"][0]["fields"][0]["display_value"] == "精装"
        assert info["maintain"]["remark"] == "钥匙在门店"
        assert info["follows"][0]["content"] == "真实在租房东，租带卖，附近最有性价比的电梯套三"
        assert info["follows"][0]["remarks"] == "业主脾气好"
        assert info["follows"][0]["on_top"] is True
        assert [call.route for call in session.calls] == [
            "rental_listing.get_hdic_info",
            "rental_listing.get_house_label",
            "rental_listing.get_hqi_tab",
            "rental_listing.get_maintain_info",
            "rental_listing.get_follow",
        ]


@pytest.mark.anyio(backend="asyncio")
async def test_upstream_invalid_input_surfaces_as_tool_error(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "rental_listing.search", 200,
        {"code": 100001, "msg": "key列表不能为空", "data": {}},
    )
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_search",
            {"input": {"community_keyword": "", "page": 1, "page_size": 20}},
        )
        assert result.is_error is True
        assert "CRM_UPSTREAM_INVALID_INPUT" in _first_text(result)


@pytest.mark.anyio(backend="asyncio")
async def test_unknown_input_fields_are_rejected_before_upstream(tmp_path) -> None:
    session = StubSession()
    server = _wired_server(session, tmp_path)

    async with Client(server) as client:
        result = await client.call_tool(
            "rental_listing_search",
            {"input": {"community_keyword": "万象城", "bogus_field": 1}},
        )
        assert result.is_error is True
        # The bogus field must be rejected by the input model before any
        # upstream request is issued.
        assert session.calls == []


@pytest.mark.anyio(backend="asyncio")
async def test_auth_required_surfaces_as_tool_error(tmp_path) -> None:
    server = _wired_server(AuthRequiredSession(), tmp_path)

    async with Client(server) as client:
        result = await client.call_tool("crm_whoami", {})
        assert result.is_error is True
        assert "CRM_AUTH_REQUIRED" in _first_text(result)


@pytest.mark.anyio(backend="asyncio")
async def test_gated_tools_are_rate_limited(tmp_path) -> None:
    session = StubSession()
    session.enqueue(
        "identity.me", 200, {"code": 100000, "data": {"ucid": "100000003", "name": "张三"}}
    )
    server = _wired_server(session, tmp_path, rate_limit=1)

    async with Client(server) as client:
        first = await client.call_tool("crm_whoami", {})
        assert first.is_error is False
        second = await client.call_tool("crm_whoami", {})
        assert second.is_error is True
        assert "RATE_LIMITED" in _first_text(second)


def test_tool_metadata_exposes_input_and_output_schemas() -> None:
    by_name = {tool.name: tool for tool in tool_definitions()}
    search = by_name["rental_listing_search"]
    # MCP 2.0 nests the single model argument under "input".
    assert "input" in search.input_schema["properties"]
    assert search.input_schema["required"] == ["input"]
    # Output schema is present and points at the page envelope.
    assert search.output_schema is not None
    assert "items" in search.output_schema["properties"]
    assert search.as_dict()["outputSchema"] is not None
    # Unrelated tools have no input parameters.
    assert by_name["crm_connection_status"].input_schema["properties"] == {}
    assert by_name["crm_whoami"].input_schema["properties"] == {}
    nearby = by_name["rental_map_nearby_search"]
    assert "input" in nearby.input_schema["properties"]
    assert nearby.output_schema is not None
    assert "matched_community_count" in nearby.output_schema["properties"]
    assert "community_ids" in nearby.output_schema["properties"]
    assert "community_ids_truncated" in nearby.output_schema["properties"]
    assert nearby.module_id == "property.rental.map_search"
    assert by_name["rental_listing_search"].module_id == "property.rental.listing_search"
    assert by_name["sale_listing_search"].module_id == "property.sale.listing_search"
    assert by_name["sale_listing_get_detail_head"].module_id == "property.sale.listing_detail"
    sale_nearby = by_name["sale_map_nearby_search"]
    assert sale_nearby.output_schema is not None
    assert "community_ids_truncated" in sale_nearby.output_schema["properties"]
    assert "$ref" not in json.dumps(by_name["sale_listing_search"].as_dict())
    tuoguan_detail = by_name["tuoguan_listing_get_detail"]
    assert "input" in tuoguan_detail.input_schema["properties"]
    assert "prospects" in tuoguan_detail.output_schema["properties"]
    assert tuoguan_detail.module_id == "property.rental.tuoguan"
    assert by_name["tuoguan_listing_get_deals"].module_id == "property.rental.tuoguan"
    assert "$ref" not in json.dumps(tuoguan_detail.as_dict())


@pytest.mark.anyio(backend="asyncio")
async def test_stdio_transport_serves_tools_from_real_entry_point(tmp_path) -> None:
    """The packaged entry point (app.mcp.server:main) must serve tools over
    stdio from a clean subprocess with the default unconfigured profile."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(SERVICE_DIR),
        env={
            **os.environ,
            "CC_UPSTREAM_PROFILE": "unconfigured",
            "CC_MCP_TRANSPORT": "stdio",
            "PYTHONIOENCODING": "utf-8",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert sorted(tool.name for tool in tools.tools) == [
                "crm_connection_status",
                "crm_whoami",
                "rental_listing_filter_options",
                "rental_listing_get_detail",
                "rental_listing_get_house_info",
                "rental_listing_get_prospect",
                "rental_listing_search",
                "rental_map_nearby_search",
                "rental_map_suggest",
                "sale_community_suggest",
                "sale_listing_filter_options",
                "sale_listing_get_detail",
                "sale_listing_get_detail_head",
                "sale_listing_get_follows",
                "sale_listing_get_maintain_info",
                "sale_listing_search",
                "sale_map_nearby_search",
                "sale_map_suggest",
                "tuoguan_listing_get_deals",
                "tuoguan_listing_get_detail",
            "tuoguan_listing_search",
            ]
            result = await session.call_tool("crm_connection_status", {})
            assert result.is_error is False
            body = json.loads(_first_text(result))
            # No credential on the unconfigured profile: the stub reports
            # auth_required without ever touching the upstream.
            assert body["state"] == "auth_required"
