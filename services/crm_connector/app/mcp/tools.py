from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only},
        }


_TOOLS = (
    McpToolDefinition(
        name="crm_connection_status",
        description="Return the local CRM connector authorization and network status.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    McpToolDefinition(
        name="crm_whoami",
        description="Verify the CRM principal currently bound to this connector.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    McpToolDefinition(
        name="rental_listing_search",
        description="Search rental listings using structured, permission-scoped filters.",
        input_schema={
            "type": "object",
            "properties": {
                "community_keyword": {"type": "string"},
                "listing_id": {"type": "string"},
                "maintainer": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["my_maintained", "shared", "role_visible"],
                    "default": "my_maintained",
                },
                "districts": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "monthly_rent_yuan": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number", "minimum": 0},
                        "max": {"type": "number", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "area_sqm": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number", "minimum": 0},
                        "max": {"type": "number", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "rooms": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                "orientations": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    ),
    McpToolDefinition(
        name="rental_listing_get_detail",
        description="Retrieve one rental listing by its CRM listing identifier.",
        input_schema={
            "type": "object",
            "properties": {"listing_id": {"type": "string", "minLength": 1}},
            "required": ["listing_id"],
            "additionalProperties": False,
        },
    ),
)


def tool_definitions() -> tuple[McpToolDefinition, ...]:
    return _TOOLS
