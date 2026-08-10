"""MCP-facing input models and re-exports of API output models.

MCP tool inputs are stricter than the HTTP API: unknown fields are rejected
(``extra="forbid"``) so a sloppy agent payload surfaces a validation error
instead of silently reaching the upstream with ignored fields.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas import (
    ConnectionStatusResponse,
    ListingDetailInfoResponse,
    ListingProspectResponse,
    PrincipalResponse,
    RentalListingFilterOptionResponse,
    RentalListingPageResponse,
    RentalListingResponse,
    RentalListingSearchRequest,
    RentalMapBubbleRequest,
    RentalMapBubbleResponse,
    RentalMapNearbySearchRequest,
    RentalMapNearbySearchResponse,
    RentalMapPageResponse,
    RentalMapSearchRequest,
    RentalMapSuggestionRequest,
    RentalMapSuggestionResponse,
)

__all__ = [
    "ConnectionStatusResponse",
    "ListingDetailInfoResponse",
    "ListingProspectResponse",
    "PrincipalResponse",
    "RentalListingDetailInput",
    "RentalListingFilterOptionResponse",
    "RentalListingPageResponse",
    "RentalListingResponse",
    "RentalListingSearchInput",
    "RentalMapBubbleInput",
    "RentalMapBubbleResponse",
    "RentalMapNearbySearchInput",
    "RentalMapNearbySearchResponse",
    "RentalMapPageResponse",
    "RentalMapSearchInput",
    "RentalMapSuggestionInput",
    "RentalMapSuggestionResponse",
]


class RentalListingSearchInput(RentalListingSearchRequest):
    """Search arguments accepted by the ``rental_listing_search`` MCP tool."""

    model_config = ConfigDict(extra="forbid")


class RentalListingDetailInput(BaseModel):
    """Arguments accepted by the ``rental_listing_get_detail`` MCP tool."""

    listing_id: str = Field(min_length=1, max_length=64)
    model_config = ConfigDict(extra="forbid")


class RentalMapSearchInput(RentalMapSearchRequest):
    """Low-level viewport or known-community map search."""

    model_config = ConfigDict(extra="forbid")


class RentalMapBubbleInput(RentalMapBubbleRequest):
    """Return map bubbles for a known geographic viewport."""

    model_config = ConfigDict(extra="forbid")


class RentalMapSuggestionInput(RentalMapSuggestionRequest):
    """Resolve a landmark, business circle, or community on the rental map."""

    model_config = ConfigDict(extra="forbid")


class RentalMapNearbySearchInput(RentalMapNearbySearchRequest):
    """Search around a named location using a community-centroid radius."""

    model_config = ConfigDict(extra="forbid")
