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
    PrincipalResponse,
    RentalListingPageResponse,
    RentalListingResponse,
)
from app.bootstrap import build_service
from app.domain.errors import ConnectorError
from app.infrastructure.settings import Settings, load_settings
from app.mcp.rate_limit import RateLimiter
from app.mcp.schemas import RentalListingDetailInput, RentalListingSearchInput

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
        name="rental_listing_search",
        description="Search rental listings using structured, permission-scoped filters.",
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
