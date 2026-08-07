"""MCP-facing input models and re-exports of API output models.

MCP tool inputs are stricter than the HTTP API: unknown fields are rejected
(``extra="forbid"``) so a sloppy agent payload surfaces a validation error
instead of silently reaching the upstream with ignored fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import (
    ConnectionStatusResponse,
    PrincipalResponse,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalListingSearchRequest,
)

__all__ = [
    "ConnectionStatusResponse",
    "PrincipalResponse",
    "RentalListingDetailInput",
    "RentalListingPageResponse",
    "RentalListingResponse",
    "RentalListingSearchInput",
]


class RentalListingSearchInput(RentalListingSearchRequest):
    """Search arguments accepted by the ``rental_listing_search`` MCP tool."""

    model_config = ConfigDict(extra="forbid")


class RentalListingDetailInput(BaseModel):
    """Arguments accepted by the ``rental_listing_get_detail`` MCP tool."""

    listing_id: str = Field(min_length=1, max_length=64)
    model_config = ConfigDict(extra="forbid")
