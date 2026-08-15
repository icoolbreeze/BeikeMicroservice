from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.mcp.schema_flatten import flatten_schema

from app.mcp.schemas import (
    ConnectionStatusResponse,
    ListingDetailInfoResponse,
    ListingProspectResponse,
    PrincipalResponse,
    RentalListingDetailInput,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalListingSearchInput,
    RentalMapNearbySearchInput,
    RentalMapNearbySearchResponse,
    RentalMapSuggestionInput,
    RentalMapSuggestionResponse,
    SaleCommunitySuggestInput,
    SaleCommunitySuggestionResponse,
    SaleFollowRecordResponse,
    SaleListingDetailInput,
    SaleListingDetailResponse,
    SaleListingPageResponse,
    SaleListingResponse,
    SaleListingSearchInput,
    SaleMaintainInfoResponse,
    SaleMapNearbySearchInput,
    SaleMapNearbySearchResponse,
    SaleMapSuggestInput,
    SaleMapSuggestionResponse,
    TrusteeshipDealPageResponse,
    TrusteeshipDealsInput,
    TrusteeshipDetailInput,
    TrusteeshipDetailResponse,
)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Mirror how MCP 2.0 nests a single pydantic argument under ``input``."""
    return {
        "type": "object",
        "properties": {"input": flatten_schema(_schema(model))},
        "required": ["input"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    read_only: bool = True
    module_id: str = "platform.connection"

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            # Keep the diagnostic HTTP catalog identical to the stdio
            # transport: MCP clients commonly forward this schema directly
            # to an LLM tool definition, where $ref/$defs are not portable.
            "inputSchema": flatten_schema(self.input_schema),
            "annotations": {"readOnlyHint": self.read_only},
            "moduleId": self.module_id,
        }
        if self.output_schema is not None:
            result["outputSchema"] = flatten_schema(self.output_schema)
        return result


_TOOLS = (
    McpToolDefinition(
        name="crm_connection_status",
        description="Return the local CRM connector authorization and network status.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(ConnectionStatusResponse),
    ),
    McpToolDefinition(
        name="crm_whoami",
        description="Verify the CRM principal currently bound to this connector.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(PrincipalResponse),
    ),
    McpToolDefinition(
        name="rental_listing_filter_options",
        description=(
            "Return the current 房源列表（全部房源） filter catalog as a compact "
            "text digest: every condition group key, name, selection type, and its "
            "enumeration values (truncated). Large 区域/商圈 enumerations are "
            "deliberately not expanded — resolve places with rental_map_suggest "
            "instead."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "string"},
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_listing_search",
        description=(
            "Search rental listings using structured, permission-scoped filters. "
            "scope defaults to my_maintained (维护盘) — when the user gave no "
            "scope restriction, pass scope=\"all\" (不限) explicitly so the "
            "search is not silently limited to the maintained pool. "
            "For exact community filtering, resolve names with rental_map_suggest "
            "and pass selected resblock item_id values as resblock_ids. "
            "budget_yuan calculates price as [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound. "
            "Image URLs (title_image_url / floor_plan_image_url) are raw "
            "img.ljcdn.com originals — direct fetch may return 403 "
            "(inspection/floor-plan buckets are protected; cover-image bucket "
            "is public). To view, append a size suffix yourself: .450x.jpg "
            "(thumbnail), .750x.jpg, .800x.jpg, .1500x.jpg (highest quality); "
            "suffixed variants are always public, no credentials needed "
            "(see docs/rental-image-cdn.md)."
        ),
        input_schema=_input_schema(RentalListingSearchInput),
        output_schema=_schema(RentalListingPageResponse),
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_listing_get_detail",
        description="Retrieve one rental listing by its CRM listing identifier.",
        input_schema=_input_schema(RentalListingDetailInput),
        output_schema=_schema(RentalListingResponse),
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_listing_get_prospect",
        description=(
            "Return one rental listing's detail-page 实勘 record: survey photos, "
            "floor plan, and edit permission. has_survey_photo=false with an "
            "empty photos list means the house has not been surveyed yet. "
            "photos[].url and floor_plan_url are raw img.ljcdn.com originals — "
            "direct fetch may return 403 (inspection/floor-plan buckets are "
            "protected). To view an image, append a size suffix yourself: "
            ".450x.jpg (thumbnail), .750x.jpg, .800x.jpg, .1500x.jpg (highest "
            "quality); suffixed variants are always public, no credentials "
            "needed (see docs/rental-image-cdn.md)."
        ),
        input_schema=_input_schema(RentalListingDetailInput),
        output_schema=_schema(ListingProspectResponse),
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_listing_get_house_info",
        description=(
            "Return one rental listing's detail-page information beyond the "
            "head record: labels (电梯房/VR房/…), 小区/楼栋 attributes "
            "(楼型、梯户比、物业费、水电气、车位比、停车费…), the HQI "
            "quality score, the 维护信息 (家具/家电/租期/装修/入住时间/备注… "
            "with render-ready display values), and the 跟进记录 (follows) — "
            "the keeper's field notes on the house's latest state, useful for "
            "vetting a listing before showing it. Verified across 50 real "
            "listings (2026-08-09), follow content carries: viewing logistics "
            "(密码锁/实体钥匙, 钥匙在哪个门店, 随时可看 vs 需提前通知, "
            "可否马上看房), price leverage (业主底价/佣金让步/杂费明细 like "
            "水电气物业网费), rental status (在租 vs 续租/空置/转租/预计空出, "
            "租带卖), restrictions and risks (暂不推荐原因, 限性别/不养宠/"
            "年付要求), and call/visit logs (回访结果, 电话接通/拒接/关机). "
            "Each record has type (普通跟进/录音跟进), author, role, time, "
            "labels (真实在租… — listing genuineness signal), remarks, and "
            "on_top marking the pinned important follow-up. Full history is "
            "returned (up to 100 records). hqi is null when the house has no "
            "score record yet; follows is empty when no follow-up exists."
        ),
        input_schema=_input_schema(RentalListingDetailInput),
        output_schema=_schema(ListingDetailInfoResponse),
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_map_suggest",
        description="Resolve a rental-map landmark, business circle, or community name.",
        input_schema=_input_schema(RentalMapSuggestionInput),
        output_schema={"type": "array", "items": _schema(RentalMapSuggestionResponse)},
        module_id="property.rental.map_search",
    ),
    McpToolDefinition(
        name="rental_map_nearby_search",
        description=(
            "Find rentals near a named place. Resolves the place, selects communities "
            "whose centroids fall within the requested radius, then searches their listings. "
            "For a stated budget, use [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound. At most 100 community ids are "
            "queried; check community_ids_truncated before treating a wide "
            "circle as complete."
        ),
        input_schema=_input_schema(RentalMapNearbySearchInput),
        output_schema=_schema(RentalMapNearbySearchResponse),
        module_id="property.rental.map_search",
    ),
    # -- 买卖 (sale, house.link) -------------------------------------------
    McpToolDefinition(
        name="sale_listing_filter_options",
        description=(
            "Return the current 买卖 全部房源 filter catalog as a compact text "
            "digest: every condition group key, name, selection type, and its "
            "enumeration values (truncated). Use it before calling "
            "sale_listing_search with unfamiliar filter keys/values."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "string"},
        module_id="property.sale.listing_search",
    ),
    McpToolDefinition(
        name="sale_community_suggest",
        description=(
            "Resolve a 买卖 community name into community identifiers. Pass the "
            "returned community_id values to sale_listing_search.community_ids "
            "for exact one-or-more-community filtering (multi_community_id)."
        ),
        input_schema=_input_schema(SaleCommunitySuggestInput),
        output_schema={"type": "array", "items": _schema(SaleCommunitySuggestionResponse)},
        module_id="property.sale.listing_search",
    ),
    McpToolDefinition(
        name="sale_listing_search",
        description=(
            "Search 在售 (买卖) listings using structured, permission-scoped "
            "filters: scope (default gdiv_mt 维护盘 — pass \"all\" 不限 "
            "explicitly when the user gave no scope), exact communities via "
            "community_ids (resolve names with sale_community_suggest first), "
            "total price in 万元, area in 平米, rooms, floors, orientations, "
            "house layouts, tags, house age, visitable times, payment mode, "
            "building type, and the 筛选 dropdown values via select. "
            "Range semantics: total_price_wan 50..70 = 50-70万, area_sqm "
            "70..110 = 70-110平, rooms [2,3] = 2-3室. Sort options: "
            "period1_desc_createtime_desc (新上优先, default) / "
            "period1_asc_totalprice / period1_desc_totalprice. "
            "Image URLs (surface_image_url / floor_plan_image_url) are raw "
            "img.ljcdn.com originals; direct fetch may 403 — append a size "
            "suffix (.450x.jpg / .750x.jpg / .800x.jpg / .1500x.jpg) to view "
            "(docs/rental-image-cdn.md)."
        ),
        input_schema=_input_schema(SaleListingSearchInput),
        output_schema=_schema(SaleListingPageResponse),
        module_id="property.sale.listing_search",
    ),
    McpToolDefinition(
        name="sale_listing_get_detail",
        description=(
            "Retrieve one 在售 listing by its 房源编号 (housedelCode) from the "
            "买卖 search rows: community, biz circle, layout, area, total price "
            "(元/文案), unit price, floor, orientation, tags, 15天带看, "
            "维护人/维护完成度, quality score, holder level, 地铁, VR 状态."
        ),
        input_schema=_input_schema(SaleListingDetailInput),
        output_schema=_schema(SaleListingResponse),
        module_id="property.sale.listing_detail",
    ),
    McpToolDefinition(
        name="sale_listing_get_detail_head",
        description=(
            "Return one 在售 listing's full detail head (housedel/views): "
            "base record (价格/户型/楼层/朝向/房屋等级/维护人与门店/挂牌天数/"
            "来源/业主预期价/库存分) plus 小区/楼栋 attributes (城区/商圈/建筑"
            "年代与类型/交易权属/物业费/水电气/供暖/车位/电梯/梯户比/学区/"
            "产权年限/凶宅嫌恶) plus 外网呈现 links (lianjia/beike/vr)."
        ),
        input_schema=_input_schema(SaleListingDetailInput),
        output_schema=_schema(SaleListingDetailResponse),
        module_id="property.sale.listing_detail",
    ),
    McpToolDefinition(
        name="sale_listing_get_maintain_info",
        description=(
            "Return one 在售 listing's detail-page 维护信息 (getMaintainInfo): "
            "grouped modules (看房信息/价格信息/业主信息/特色信息) whose fields "
            "carry render-ready display values (房屋现状=空置, 是否唯一=不唯一, "
            "户口情况=无户口可迁入, 抵押情况=无抵押, 产权共有, 是否合同房, "
            "产权面积, 是否满N…) plus the important-field summary and "
            "完备率 (e.g. 9/9)."
        ),
        input_schema=_input_schema(SaleListingDetailInput),
        output_schema=_schema(SaleMaintainInfoResponse),
        module_id="property.sale.listing_detail",
    ),
    McpToolDefinition(
        name="sale_listing_get_follows",
        description=(
            "Return one 在售 listing's detail-page 跟进记录 (queryfollows): the "
            "keeper's field notes on the house's latest state (看房便利/钥匙/"
            "价格筹码/业主态度/交易进度…), each with author (+角色/门店), "
            "time text, on_top (置顶重点跟进), remarks, follow_label."
        ),
        input_schema=_input_schema(SaleListingDetailInput),
        output_schema={"type": "array", "items": _schema(SaleFollowRecordResponse)},
        module_id="property.sale.listing_detail",
    ),
    # -- 买卖 地图找房 (sale mapSearch) ------------------------------------
    McpToolDefinition(
        name="sale_map_suggest",
        description=(
            "Resolve a 买卖 map phrase (community, mall, landmark, road…) into "
            "coordinate-bearing community entries. Use the first match's "
            "latitude/longitude as a nearby-search centre, or pass its id to "
            "sale_map_nearby_search via center_latitude/center_longitude."
        ),
        input_schema=_input_schema(SaleMapSuggestInput),
        output_schema={"type": "array", "items": _schema(SaleMapSuggestionResponse)},
        module_id="property.sale.map_search",
    ),
    McpToolDefinition(
        name="sale_map_nearby_search",
        description=(
            "Find 在售 (买卖) listings near a named place. Resolves the place, "
            "loads community bubbles whose centroids fall within the requested "
            "radius, then searches those communities' listings (multi_community_id). "
            "At most 100 community ids are queried; inspect the response's "
            "community_ids_truncated flag before treating a wide circle as complete. "
            "scope defaults to all (不限) because a radius circle legitimately "
            "crosses pool boundaries — pass gdiv_mt/gdiv_share/… only when the "
            "user explicitly restricts to their own pools. "
            "Price is in 万元 (total_price_wan 50..70 = 50-70万), area in 平米, "
            "rooms in 室 (5 = 5室以上). The response's community_ids can be passed "
            "to sale_listing_search for the same circle with the full filter catalog."
        ),
        input_schema=_input_schema(SaleMapNearbySearchInput),
        output_schema=_schema(SaleMapNearbySearchResponse),
        module_id="property.sale.map_search",
    ),
    # -- 托管 (省心租, trusteeship.link.lianjia.com) -------------------------
    McpToolDefinition(
        name="tuoguan_listing_get_detail",
        description=(
            "Return one 托管 (省心租·自营, del_type=5) listing's detail-page "
            "head from the trusteeship workbench (pageInfoForPc): 小区/栋/室 "
            "names, 户型 (1-0-1-1), 面积, 指导租金 guide_price_yuan, 朝向/楼层, "
            "可入住时间/看房时间/租期要求, 托管到期日/延长期, tags (钻石好房/"
            "贝壳省心租·自营/预约券…), 房管人 manager (姓名/门店/电话), "
            "钥匙形态 key_desc (智能门锁…), 外网呈现, 实勘照片 prospects "
            "(per-room name + url + upload time + primary_flag 封面), 户型图 "
            "house_type_images, VR link vr_url, HQI score, 成交参考 "
            "deal_details + 平均价/总数, and 费用项 fee_groups (服务费比例/"
            "押金/租期矩阵, ' | ' joined rows). Use it for 托管 rows from "
            "rental_listing_search — the 普租 detail tools (rental_listing_"
            "get_detail / _prospect / _house_info) do NOT serve 托管 ids. "
            "Photo URLs are img.ljcdn.com lease-image bucket originals; the "
            "page appends an instruction suffix like "
            "'!m_fit,h_630,w_516,l_bk,f_jpg' for the watermarked public "
            "variant — append the same suffix yourself to download (see "
            "docs/rental-image-cdn.md)."
        ),
        input_schema=_input_schema(TrusteeshipDetailInput),
        output_schema=_schema(TrusteeshipDetailResponse),
        module_id="property.rental.tuoguan",
    ),
    McpToolDefinition(
        name="tuoguan_listing_get_deals",
        description=(
            "Return one 托管 listing's 成交参考 history (deal/list): each row "
            "carries 成交价 deal_price, 成交时间 deal_time, 面积/朝向/楼层 desc, "
            "实勘图 prospect_url, 户型图 layout_url, and 上租时间 on_rent_time. "
            "Paginated (page/page_size, defaults 1/5); has_more tells whether "
            "another page exists. Empty result means no deal record yet — a "
            "valid answer, not an error."
        ),
        input_schema=_input_schema(TrusteeshipDealsInput),
        output_schema=_schema(TrusteeshipDealPageResponse),
        module_id="property.rental.tuoguan",
    ),
)


def tool_definitions() -> tuple[McpToolDefinition, ...]:
    return _TOOLS
