"""MCP stdio transport exposing the CRM connector's read-only tools.

Entry point for the ``crm-mcp`` console script. Tools are all read-only and
rate-limited per local caller; authentication errors surface as ToolError
without ever disclosing credential material.
"""

from __future__ import annotations

import getpass
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from app.api.schemas import (
    ConnectionStatusResponse,
    ListingDetailInfoResponse,
    ListingProspectResponse,
    PrincipalResponse,
    RentalListingFilterOptionResponse,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalMapNearbySearchResponse,
    RentalMapSuggestionResponse,
)
from app.bootstrap import build_service
from app.domain.errors import ConnectorError
from app.infrastructure.settings import Settings, load_settings
from app.mcp.rate_limit import RateLimiter
from app.mcp.schemas import (
    RentalListingDetailInput,
    RentalListingSearchInput,
    RentalMapNearbySearchInput,
    RentalMapSuggestionInput,
)

logger = logging.getLogger(__name__)

RATE_LIMITED_CODE = "RATE_LIMITED"


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
            "Read-only CRM rental listing queries bound to the logged-in employee. "
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
            "Return the current 房源列表（全部房源） filter catalog and valid "
            "page-native condition values."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def rental_listing_filter_options() -> list[RentalListingFilterOptionResponse]:
        _require_quota(_caller_subject())
        try:
            options = service.rental_listing_filter_options()
            return [RentalListingFilterOptionResponse.from_domain(option) for option in options]
        except ConnectorError as exc:
            raise _tool_error(exc) from exc

    @server.tool(
        name="rental_listing_search",
        description=(
            "Search rental listings using structured, permission-scoped filters. "
            "For an exact community, call rental_map_suggest first and pass the "
            "selected resblock item_id in resblock_ids. "
            "budget_yuan calculates price as [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound."
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
            "empty photos list means the house has not been surveyed yet."
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
            "For a stated budget, use [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound."
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
