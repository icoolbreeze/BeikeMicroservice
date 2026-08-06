from __future__ import annotations

from app.domain.errors import UpstreamNotConfiguredError
from app.domain.models import Principal, RentalListing, RentalListingFilters, RentalListingPage


class UnconfiguredCrmClient:
    """Safe default that never contacts an upstream CRM."""

    def whoami(self) -> Principal:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        raise UpstreamNotConfiguredError("CRM upstream routes have not been configured")
