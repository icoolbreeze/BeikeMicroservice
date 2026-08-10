from __future__ import annotations

from app.domain.errors import UpstreamNotConfiguredError
from app.domain.models import (
    ListingDetailInfo,
    ListingProspect,
    Principal,
    RentalListingFilterOption,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
    RentalMapBubble,
    RentalMapBubbleFilters,
    RentalMapPage,
    RentalMapSearchFilters,
    RentalMapSuggestion,
    RentalMapSuggestionFilters,
)


class UnconfiguredCrmClient:
    """Safe default that never contacts an upstream CRM."""

    def whoami(self) -> Principal:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def rental_listing_filter_options(self) -> tuple[RentalListingFilterOption, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_rental_listing_prospect(self, listing_id: str) -> ListingProspect:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_rental_listing_house_info(self, listing_id: str) -> ListingDetailInfo:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def rental_map_bubbles(self, filters: RentalMapBubbleFilters) -> tuple[RentalMapBubble, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")
