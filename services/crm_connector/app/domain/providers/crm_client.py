from __future__ import annotations

from typing import Protocol

from app.domain.models import Principal, RentalListing, RentalListingFilters, RentalListingPage


class CrmClient(Protocol):
    """Controlled CRM business interface; no generic HTTP proxy is permitted."""

    def whoami(self) -> Principal:
        """Return the authenticated CRM principal."""

    def search_rental_listings(self, filters: RentalListingFilters) -> RentalListingPage:
        """Return a page of rental listings within the upstream user's permissions."""

    def get_rental_listing_detail(self, listing_id: str) -> RentalListing:
        """Return a single rental listing within the upstream user's permissions."""
