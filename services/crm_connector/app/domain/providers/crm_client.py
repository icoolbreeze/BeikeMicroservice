from __future__ import annotations

from typing import Protocol

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


class CrmClient(Protocol):
    """Controlled CRM business interface; no generic HTTP proxy is permitted."""

    def whoami(self) -> Principal:
        """Return the authenticated CRM principal."""

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        """Return a page of rental listings within the upstream user's permissions."""

    def rental_listing_filter_options(self) -> tuple[RentalListingFilterOption, ...]:
        """Return the current 房源列表 filter catalog from the CRM."""

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        """Return a single rental listing within the upstream user's permissions."""

    def get_rental_listing_prospect(self, listing_id: str) -> ListingProspect:
        """Return the detail-page 实勘 (survey photo) record for one listing.

        An empty ``photos`` tuple is a valid answer — the house has not been
        surveyed yet.
        """

    def get_rental_listing_house_info(self, listing_id: str) -> ListingDetailInfo:
        """Return the aggregated detail-page information beyond detailHead.

        Labels, 小区/楼栋 attributes, and HQI score. ``hqi`` may be None
        for houses without a score record.
        """

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        """Search rental houses by viewport or a set of drawn-circle communities."""

    def rental_map_bubbles(self, filters: RentalMapBubbleFilters) -> tuple[RentalMapBubble, ...]:
        """Return district, business-circle, or community map bubbles."""

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        """Resolve a map search phrase into typed geographic targets."""
