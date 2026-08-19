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

    def get_rental_listing_house_info(
        self, listing_id: str, *, include_follows: bool = True
    ) -> ListingDetailInfo:
        """Return the aggregated detail-page information beyond detailHead.

        Labels, 小区/楼栋 attributes, and HQI score. ``hqi`` may be None
        for houses without a score record. Set ``include_follows=False`` for
        customer-facing flows so the sensitive follow-up route is never called.
        """

    def get_rental_listing_redirect_url(self, listing_id: str) -> str | None:
        """Return the trusteeship ``cell_code`` for a managed (del_type=5) row.

        Uses the same getRedirectUrl endpoint the 房源列表 page uses when an
        employee clicks a managed row; ``None`` when there is no redirect.
        """

    def search_rental_map(self, filters: RentalMapSearchFilters) -> RentalMapPage:
        """Search rental houses by viewport or a set of drawn-circle communities."""

    def rental_map_bubbles(self, filters: RentalMapBubbleFilters) -> tuple[RentalMapBubble, ...]:
        """Return district, business-circle, or community map bubbles."""

    def rental_map_suggest(
        self, filters: RentalMapSuggestionFilters
    ) -> tuple[RentalMapSuggestion, ...]:
        """Resolve a map search phrase into typed geographic targets."""

    # -- 买卖 (sale, house.link) --------------------------------------------

    def search_sale_listings(self, filters: SaleListingFilters) -> SaleListingPage:
        """Return a page of 在售 listings within the upstream user's permissions."""

    def sale_filter_options(self) -> tuple[SaleListingFilterOption, ...]:
        """Return the current 买卖 全部房源 filter catalog (getSearchFilters)."""

    def sale_community_suggest(self, query: str) -> tuple[SaleCommunitySuggestion, ...]:
        """Resolve a 买卖 community name into community identifiers."""

    def get_sale_listing_detail(self, listing_id: str) -> SaleListing:
        """Return one 在售 listing from the 买卖 search rows (allows detail
        by id without a full search round-trip)."""

    def get_sale_listing_detail_head(self, listing_id: str) -> SaleListingDetail:
        """Return the 买卖 detail head (housedel/views) plus ext info."""

    def get_sale_listing_maintain_info(self, listing_id: str) -> SaleMaintainInfo:
        """Return the 买卖 detail-page 维护信息 section (getMaintainInfo)."""

    def get_sale_listing_follows(self, listing_id: str) -> tuple[SaleFollowRecord, ...]:
        """Return the 买卖 detail-page 跟进记录 (queryfollows)."""

    def sale_map_suggest(self, query: str, city_id: str) -> tuple[SaleMapSuggestion, ...]:
        """Resolve a 买卖 map phrase into a coordinate-bearing community entry."""

    def sale_map_bubbles(
        self, filters: SaleMapBubbleFilters
    ) -> tuple[SaleMapBubble, ...]:
        """Return district or community map bubbles for a viewport rectangle."""

    # -- 托管 (省心租, trusteeship.link.lianjia.com) --------------------------

    def get_trusteeship_detail(self, cell_code: str) -> TrusteeshipDetail:
        """Return the 托管 detail-page head (pageInfoForPc).

        Covers the 实勘 photo list, 户型图, VR, 费用项, and 成交参考 that
        the 普租 detailHead domain does not serve for 托管 (del_type=5) ids.
        """

    def get_trusteeship_deals(
        self, cell_code: str, *, page: int, page_size: int
    ) -> TrusteeshipDealPage:
        """Return one page of the 托管 成交参考 (deal/list)."""

    def search_trusteeship_listings(
        self, *, page: int, page_size: int, cell_code: str | None
    ) -> TrusteeshipListingPage:
        """Return one page of the 托管 待出租 inventory (waitingrent).

        Rows carry the trusteeship ``cell_code`` (bizCode) directly usable
        with ``get_trusteeship_detail``; ``cell_code`` narrows to an exact
        unit when provided.
        """
