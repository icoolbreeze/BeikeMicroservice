from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.mcp.schemas import (
    ConnectionStatusResponse,
    PrincipalResponse,
    RentalListingFilterOptionResponse,
    RentalListingDetailInput,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalListingSearchInput,
    RentalMapNearbySearchInput,
    RentalMapNearbySearchResponse,
    RentalMapSuggestionInput,
    RentalMapSuggestionResponse,
)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Mirror how MCP 2.0 nests a single pydantic argument under ``input``."""
    return {
        "type": "object",
        "properties": {"input": _schema(model)},
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
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only},
            "moduleId": self.module_id,
        }
        if self.output_schema is not None:
            result["outputSchema"] = self.output_schema
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
            "Return the current 房源列表（全部房源） filter catalog, including "
            "the allowed page-native condition keys and values."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "array", "items": _schema(RentalListingFilterOptionResponse)},
        module_id="property.rental.listing_search",
    ),
    McpToolDefinition(
        name="rental_listing_search",
        description=(
            "Search rental listings using structured, permission-scoped filters. "
            "For exact community filtering, resolve names with rental_map_suggest "
            "and pass selected resblock item_id values as resblock_ids. "
            "budget_yuan calculates price as [budget/2, budget + clamp(25%, 200, 500)]; "
            "shared rent omits the lower bound."
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
            "shared rent omits the lower bound."
        ),
        input_schema=_input_schema(RentalMapNearbySearchInput),
        output_schema=_schema(RentalMapNearbySearchResponse),
        module_id="property.rental.map_search",
    ),
)


def tool_definitions() -> tuple[McpToolDefinition, ...]:
    return _TOOLS
