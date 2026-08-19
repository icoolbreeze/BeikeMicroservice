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
    SaleCommunitySuggestion,
    SaleFollowRecord,
    SaleListing,
    SaleListingDetail,
    SaleListingFilters,
    SaleListingFilterOption,
    SaleListingPage,
    SaleMaintainInfo,
    SaleMapBubble,
    SaleMapBubbleFilters,
    SaleMapSuggestion,
    TrusteeshipDealPage,
    TrusteeshipDetail,
    TrusteeshipListingPage,
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

    def get_rental_listing_house_info(
        self, listing_id: str, *, include_follows: bool = True
    ) -> ListingDetailInfo:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_rental_listing_redirect_url(self, listing_id: str) -> str | None:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def rental_map_bubbles(self, filters: RentalMapBubbleFilters) -> tuple[RentalMapBubble, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_sale_listings(self, filters: SaleListingFilters) -> SaleListingPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def sale_filter_options(self) -> tuple[SaleListingFilterOption, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def sale_community_suggest(self, query: str) -> tuple[SaleCommunitySuggestion, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_sale_listing_detail(self, listing_id: str) -> SaleListing:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_sale_listing_detail_head(self, listing_id: str) -> SaleListingDetail:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_sale_listing_maintain_info(self, listing_id: str) -> SaleMaintainInfo:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_sale_listing_follows(self, listing_id: str) -> tuple[SaleFollowRecord, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def sale_map_suggest(self, query: str, city_id: str) -> tuple[SaleMapSuggestion, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def sale_map_bubbles(
        self, filters: SaleMapBubbleFilters
    ) -> tuple[SaleMapBubble, ...]:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_trusteeship_detail(self, cell_code: str) -> TrusteeshipDetail:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_trusteeship_deals(
        self, cell_code: str, *, page: int, page_size: int
    ) -> TrusteeshipDealPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_trusteeship_listings(
        self, *, page: int, page_size: int, cell_code: str | None = None
    ) -> TrusteeshipListingPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")
