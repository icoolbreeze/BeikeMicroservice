from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.mcp.schemas import (
    ConnectionStatusResponse,
    PrincipalResponse,
    RentalListingDetailInput,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalListingSearchInput,
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

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only},
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
        name="rental_listing_search",
        description="Search rental listings using structured, permission-scoped filters.",
        input_schema=_input_schema(RentalListingSearchInput),
        output_schema=_schema(RentalListingPageResponse),
    ),
    McpToolDefinition(
        name="rental_listing_get_detail",
        description="Retrieve one rental listing by its CRM listing identifier.",
        input_schema=_input_schema(RentalListingDetailInput),
        output_schema=_schema(RentalListingResponse),
    ),
)


def tool_definitions() -> tuple[McpToolDefinition, ...]:
    return _TOOLS
