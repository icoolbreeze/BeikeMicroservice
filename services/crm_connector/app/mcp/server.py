"""MCP stdio transport exposing the CRM connector's read-only tools.

Entry point for the ``crm-mcp`` console script. Tools are all read-only and
rate-limited per local caller; authentication errors surface as ToolError
without ever disclosing credential material.
"""

from __future__ import annotations

import getpass
import logging
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.api.schemas import (
    ConnectionStatusResponse,
    ListingDetailInfoResponse,
    ListingProspectResponse,
    PrincipalResponse,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalMapNearbySearchResponse,
    RentalMapSuggestionResponse,
    SaleCommunitySuggestionResponse,
    SaleFollowRecordResponse,
    SaleListingDetailResponse,
    SaleListingPageResponse,
    SaleListingResponse,
    SaleMaintainInfoResponse,
    SaleMapNearbySearchResponse,
    SaleMapSuggestionResponse,
    TrusteeshipDealPageResponse,
    TrusteeshipDetailResponse,
    TrusteeshipListingPageResponse,
)
from app.bootstrap import build_service
from app.domain.errors import ConnectorError
from app.domain.models import RentalListingFilterOption, SaleListingFilterOption
from app.infrastructure.settings import Settings, load_settings
from app.mcp.rate_limit import RateLimiter
from app.mcp.schemas import (
    RentalListingDetailInput,
    RentalListingSearchInput,
    RentalMapNearbySearchInput,
    RentalMapSuggestionInput,
    SaleCommunitySuggestInput,
    SaleListingDetailInput,
    SaleListingSearchInput,
    SaleMapNearbySearchInput,
    SaleMapSuggestInput,
    TrusteeshipDealsInput,
    TrusteeshipDetailInput,
    TrusteeshipListingSearchInput,
)
from app.mcp.schema_flatten import flatten_schema

logger = logging.getLogger(__name__)

RATE_LIMITED_CODE = "RATE_LIMITED"

# filter_options 输出摘要的枚举截断阈值。全量 catalog 约 500 节点 / 318KB,
# 直接原样返回会被 Agent 端截断,拿不到任何有效信息;截断为紧凑文本后
# (~15KB)Agent 可一次性消费。区域/商圈枚举尤其庞大,明确引导走
# rental_map_suggest + resblock_ids 精确过滤。
_MAX_CATALOG_VALUES = 20
_MAX_DISTRICT_VALUES = 8
_MAX_BIZ_CIRCLE_VALUES = 5


def _format_filter_catalog(options: Sequence[RentalListingFilterOption]) -> str:
    lines = [
        f"房源列表（全部房源）filter catalog：{len(options)} 个条件组。",
        "用法：把条件组 key 填入 rental_listing_search 的 condition_filters，",
        "value 取下方枚举的 value 值；预算直接填 budget_yuan 参数。",
        "区域/商圈（districtId）枚举庞大，不要逐值枚举：先用 rental_map_suggest",
        "解析地点名称，取结果的 resblock item_id 填入 resblock_ids，",
        "或直接用 community_keyword 传小区名关键词。",
        "",
    ]
    for option in options:
        head = f"{option.key} · {option.name} · {option.selection_type}"
        values = option.children
        if not values:
            lines.append(f"[{head}] 无子枚举")
            continue
        if option.key == "districtId":
            parts = []
            for district in values[: _MAX_DISTRICT_VALUES]:
                biz_circles = " ".join(
                    f"{b.name}={b.value}" for b in district.children[:_MAX_BIZ_CIRCLE_VALUES]
                )
                extra = (
                    f"(+{len(district.children) - _MAX_BIZ_CIRCLE_VALUES}商圈)"
                    if len(district.children) > _MAX_BIZ_CIRCLE_VALUES
                    else ""
                )
                parts.append(f"{district.name}({biz_circles}{extra})")
            if len(values) > _MAX_DISTRICT_VALUES:
                parts.append(f"(+{len(values) - _MAX_DISTRICT_VALUES}区)")
            lines.append(f"[{head}] {'; '.join(parts)}")
            continue
        rendered = " ".join(f"{child.name}={child.value}" for child in values[:_MAX_CATALOG_VALUES])
        extra = (
            f"(+{len(values) - _MAX_CATALOG_VALUES}值，可用 rental_map_suggest / community_keyword 精确过滤)"
            if len(values) > _MAX_CATALOG_VALUES
            else ""
        )
        lines.append(f"[{head}] {rendered}{extra}")
    return "\n".join(lines)


def _format_sale_catalog(options: Sequence[SaleListingFilterOption]) -> str:
    lines = [
        "买卖 全部房源 filter catalog（getSearchFilters）：",
        "用法：把条件组 key 填到 sale_listing_search 的对应字段；",
        "筛选 dropdown 用 select 参数（key=value，value 取下方枚举 id）。",
        "小区精确过滤用 sale_community_suggest 解析后再填 community_ids。",
        "",
    ]
    for option in options:
        head = f"{option.key} · {option.name} · {option.selection_type}"
        if option.key == "select":
            lines.append(f"[{head}] 筛选 dropdown 组：")
            for child in option.children:
                values = " ".join(
                    f"{item.name}={item.value}" for item in child.children
                )
                extra = (
                    f"(+{len(child.children) - 20}值)" if len(child.children) > 20 else ""
                )
                lines.append(f"  - {child.name} (key={child.key}): {values}{extra}")
            continue
        option_values = option.children
        if not option_values:
            lines.append(f"[{head}] 无子枚举")
            continue
        rendered = " ".join(
            f"{child.name}={child.value}" for child in option_values[:_MAX_CATALOG_VALUES]
        )
        extra = (
            f"(+{len(option_values) - _MAX_CATALOG_VALUES}值)"
            if len(option_values) > _MAX_CATALOG_VALUES
            else ""
        )
        lines.append(f"[{head}] {rendered}{extra}")
    return "\n".join(lines)


def build_mcp_server(service, settings: Settings) -> MCPServer:
    """Create the MCP server wired to the connector service and its limits."""
    limiter = RateLimiter(settings.mcp_rate_limit_per_min)

    def _caller_subject() -> str:
        # stdio transport has no authenticated caller identity; the local
        # Windows user is the closest subject for quota attribution.
        return getpass.getuser() or "unknown"

    def _require_quota(subject: str) -> None:
        if not limiter.allow(subject):
            raise ToolError(
                f"{RATE_LIMITED_CODE}: quota of {settings.mcp_rate_limit_per_min}/min exceeded"
            )

    def _tool_error(exc: ConnectorError) -> ToolError:
        return ToolError(f"{exc.code}: {exc}")

    server = MCPServer(
        name="crm-connector",
        instructions=(
            "Read-only CRM rental and sale listing queries bound to the logged-in employee. "
            "All tools are read-only and rate-limited."
        ),
    )

    @server.tool(
        name="crm_connection_status",
        description="Return the local CRM connector authorization and network status.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def crm_connection_status() -> ConnectionStatusResponse:
        try:
            return ConnectionStatusResponse.from_domain(service.connection_status())
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="crm_whoami",
        description="Verify the CRM principal currently bound to this connector.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def crm_whoami() -> PrincipalResponse:
        _require_quota(_caller_subject())
        try:
            return PrincipalResponse.from_domain(service.whoami())
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_listing_filter_options",
        description=(
            "Return the current 房源列表（全部房源） filter catalog as a compact "
            "text digest: every condition group key, name, selection type, and its "
            "enumeration values (truncated). Large 区域/商圈 enumerations are "
            "deliberately not expanded — resolve places with rental_map_suggest "
            "instead."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_filter_options() -> str:
        _require_quota(_caller_subject())
        try:
            options = service.rental_listing_filter_options()
            return _format_filter_catalog(options)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_listing_search",
        description=(
            "Search rental listings using structured, permission-scoped filters. "
            "scope defaults to my_maintained (维护盘) — when the user gave no "
            "scope restriction, pass scope=\"all\" (不限) explicitly so the "
            "search is not silently limited to the maintained pool. "
            "For an exact community, call rental_map_suggest first and pass the "
            "selected resblock item_id in resblock_ids. "
            "budget_yuan calculates price as [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound. "
            "Rows mix 普租 (del_type=2) and 托管/省心租 (del_type=5) by default — "
            "do NOT add a delType filter unless the user explicitly restricts the "
            "type. 托管 rows are not served by the 普租 detail tools: resolve their "
            "cell_code via tuoguan_listing_search (search rows do not carry it) and "
            "use tuoguan_listing_get_detail / tuoguan_listing_get_deals instead. "
            "Image URLs (title_image_url / floor_plan_image_url) are raw "
            "img.ljcdn.com originals — direct fetch may return 403 "
            "(inspection/floor-plan buckets are protected; cover-image bucket "
            "is public). To view, append a size suffix yourself: .450x.jpg "
            "(thumbnail), .750x.jpg, .800x.jpg, .1500x.jpg (highest quality); "
            "suffixed variants are always public, no credentials needed "
            "(see docs/rental-image-cdn.md)."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_search(input: RentalListingSearchInput) -> RentalListingPageResponse:
        _require_quota(_caller_subject())
        try:
            result = service.search_rental_listings(input.to_domain())
            return RentalListingPageResponse.from_domain(result)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_listing_get_detail",
        description="Retrieve one rental listing by its CRM listing identifier.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_get_detail(input: RentalListingDetailInput) -> RentalListingResponse:
        _require_quota(_caller_subject())
        try:
            listing = service.get_rental_listing_detail(input.listing_id)
            return RentalListingResponse.from_domain(listing)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
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
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_get_prospect(
        input: RentalListingDetailInput,
    ) -> ListingProspectResponse:
        _require_quota(_caller_subject())
        try:
            prospect = service.get_rental_listing_prospect(input.listing_id)
            return ListingProspectResponse.from_domain(prospect)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
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
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_get_house_info(
        input: RentalListingDetailInput,
    ) -> ListingDetailInfoResponse:
        _require_quota(_caller_subject())
        try:
            info = service.get_rental_listing_house_info(input.listing_id)
            return ListingDetailInfoResponse.from_domain(info)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_map_suggest",
        description="Resolve a rental-map landmark, business circle, or community name.",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_map_suggest(input: RentalMapSuggestionInput) -> list[RentalMapSuggestionResponse]:
        _require_quota(_caller_subject())
        try:
            results = service.rental_map_suggest(input.to_domain(service.default_city_id))
            return [RentalMapSuggestionResponse.from_domain(item) for item in results]
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_map_nearby_search",
        description=(
            "Find rentals near a named place using community centroids within a radius. "
            "No budget_yuan parameter on this tool: convert a stated budget yourself "
            "into price_min_yuan/price_max_yuan as [budget/2, budget + clamp(25%, 200, "
            "500)] (shared rent omits the lower bound). At most 100 community ids are "
            "queried; check community_ids_truncated before treating a wide "
            "circle as complete."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_map_nearby_search(
        input: RentalMapNearbySearchInput,
    ) -> RentalMapNearbySearchResponse:
        _require_quota(_caller_subject())
        try:
            result = service.search_rental_map_nearby(
                input.to_domain(service.default_city_id)
            )
            return RentalMapNearbySearchResponse.from_domain(result)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    # -- 买卖 (sale, house.link) --------------------------------------------

    @server.tool(
        name="sale_listing_filter_options",
        description=(
            "Return the current 买卖 全部房源 filter catalog as a compact text "
            "digest: every condition group key, name, selection type, and its "
            "enumeration values (truncated)."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_filter_options() -> str:
        _require_quota(_caller_subject())
        try:
            options = service.sale_filter_options()
            return _format_sale_catalog(options)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_community_suggest",
        description=(
            "Resolve a 买卖 community name into community identifiers; pass the "
            "community_id values to sale_listing_search.community_ids."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_community_suggest(
        input: SaleCommunitySuggestInput,
    ) -> list[SaleCommunitySuggestionResponse]:
        _require_quota(_caller_subject())
        try:
            results = service.sale_community_suggest(input.query)
            return [SaleCommunitySuggestionResponse.from_domain(item) for item in results]
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_listing_search",
        description=(
            "Search 在售 (买卖) listings using structured, permission-scoped "
            "filters (scope defaults to gdiv_mt 维护盘 — pass \"all\" 不限 "
            "explicitly when the user gave no scope). Resolve exact communities "
            "with sale_community_suggest first and pass the community_id values "
            "in community_ids. The upstream returns a fixed 30 rows per page "
            "(pageSize is ignored — verified 2026-08-16), so there is no "
            "page_size parameter; page through with page."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_search(input: SaleListingSearchInput) -> SaleListingPageResponse:
        _require_quota(_caller_subject())
        try:
            result = service.search_sale_listings(input.to_domain())
            return SaleListingPageResponse.from_domain(result)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_listing_get_detail",
        description="Retrieve one 在售 listing by its 房源编号 (housedelCode).",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_get_detail(input: SaleListingDetailInput) -> SaleListingResponse:
        _require_quota(_caller_subject())
        try:
            listing = service.get_sale_listing_detail(input.listing_id)
            return SaleListingResponse.from_domain(listing)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_listing_get_detail_head",
        description=(
            "Return one 在售 listing's full detail head (housedel/views) plus "
            "外网呈现 links."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_get_detail_head(
        input: SaleListingDetailInput,
    ) -> SaleListingDetailResponse:
        _require_quota(_caller_subject())
        try:
            detail = service.get_sale_listing_detail_head(input.listing_id)
            return SaleListingDetailResponse.from_domain(detail)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_listing_get_maintain_info",
        description=(
            "Return one 在售 listing's detail-page 维护信息 (getMaintainInfo): "
            "grouped modules with render-ready display values plus the "
            "important-field summary and 完备率."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_get_maintain_info(
        input: SaleListingDetailInput,
    ) -> SaleMaintainInfoResponse:
        _require_quota(_caller_subject())
        try:
            info = service.get_sale_listing_maintain_info(input.listing_id)
            return SaleMaintainInfoResponse.from_domain(info)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_listing_get_follows",
        description=(
            "Return one 在售 listing's detail-page 跟进记录 (queryfollows)."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_listing_get_follows(
        input: SaleListingDetailInput,
    ) -> list[SaleFollowRecordResponse]:
        _require_quota(_caller_subject())
        try:
            records = service.get_sale_listing_follows(input.listing_id)
            return [SaleFollowRecordResponse.from_domain(record) for record in records]
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    # -- 买卖 地图找房 (sale mapSearch) --------------------------------------

    @server.tool(
        name="sale_map_suggest",
        description=(
            "Resolve a 买卖 map phrase into coordinate-bearing community entries."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_map_suggest(input: SaleMapSuggestInput) -> list[SaleMapSuggestionResponse]:
        _require_quota(_caller_subject())
        try:
            results = service.sale_map_suggest(input.query)
            return [SaleMapSuggestionResponse.from_domain(item) for item in results]
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="sale_map_nearby_search",
        description=(
            "Find 在售 (买卖) listings near a named place using community "
            "centroids within a radius. At most 100 community ids are queried; "
            "check community_ids_truncated for wide circles."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def sale_map_nearby_search(
        input: SaleMapNearbySearchInput,
    ) -> SaleMapNearbySearchResponse:
        _require_quota(_caller_subject())
        try:
            result = service.search_sale_map_nearby(input.to_domain())
            return SaleMapNearbySearchResponse.from_domain(result)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    # -- 托管 (省心租, trusteeship.link.lianjia.com) --------------------------

    @server.tool(
        name="tuoguan_listing_get_detail",
        description=(
            "Return one 托管 (省心租·自营, del_type=5) listing's detail-page "
            "head: 实勘照片 prospects, 户型图, VR, 费用项, 成交参考 (deal_details "
            "plus numeric deal_avg_price 元/月 and deal_total_count), 房管人, "
            "托管到期/租期/看房时间 — the 普租 detail tools do not serve 托管 ids."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def tuoguan_listing_get_detail(
        input: TrusteeshipDetailInput,
    ) -> TrusteeshipDetailResponse:
        _require_quota(_caller_subject())
        try:
            detail = service.get_trusteeship_detail(input.cell_code)
            return TrusteeshipDetailResponse.from_domain(detail)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="tuoguan_listing_get_deals",
        description=(
            "Return one 托管 listing's 成交参考 history (deal/list), paginated."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def tuoguan_listing_get_deals(
        input: TrusteeshipDealsInput,
    ) -> TrusteeshipDealPageResponse:
        _require_quota(_caller_subject())
        try:
            page = service.get_trusteeship_deals(
                input.cell_code, page=input.page, page_size=input.page_size
            )
            return TrusteeshipDealPageResponse.from_domain(page)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="tuoguan_listing_search",
        description=(
            "Search the 托管 (省心租) 待出租 inventory (city-wide waitingrent). "
            "Rows carry a working cell_code for tuoguan_listing_get_detail, "
            "plus community, layout, area, floor, 指导租金, and 看房时间. "
            "Optional cell_code narrows to one exact unit; page_size is "
            "server-capped at 300."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def tuoguan_listing_search(
        input: TrusteeshipListingSearchInput,
    ) -> TrusteeshipListingPageResponse:
        _require_quota(_caller_subject())
        try:
            page = service.search_trusteeship_listings(
                page=input.page,
                page_size=input.page_size,
                cell_code=input.cell_code,
            )
            return TrusteeshipListingPageResponse.from_domain(page)
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    # pydantic model_json_schema() 生成的 inputSchema 含 $defs/$ref 引用，
    # 多数 LLM 客户端(OpenAI 风格 tools schema)不支持引用,模型看不到真实
    # 字段会导致参数构造失败(missing input / 编造 filter key)。注册后统一
    # 展开为自包含的扁平 schema,任何客户端都能正确解析。
    for tool in server._tool_manager._tools.values():
        tool.parameters = flatten_schema(tool.parameters)

    return server


def main() -> None:
    """Console entry point for ``crm-mcp``."""
    settings = load_settings()
    if settings.mcp_transport != "stdio":
        raise SystemExit(
            f"CC_MCP_TRANSPORT={settings.mcp_transport} is not supported; "
            "crm-mcp speaks stdio only"
        )
    server = build_mcp_server(build_service(settings), settings)
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
