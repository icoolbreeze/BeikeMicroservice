from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ConnectionState(str, Enum):
    READY = "ready"
    EXPIRING = "expiring"
    AUTH_REQUIRED = "auth_required"
    NETWORK_REQUIRED = "network_required"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class ProviderStatus:
    state: ConnectionState
    message: str


@dataclass(frozen=True)
class ConnectionStatus:
    state: ConnectionState
    message: str
    bound_employee_principal: str | None
    mcp_transport: str
    checked_at: datetime


@dataclass(frozen=True)
class Principal:
    employee_principal: str
    display_name: str | None = None


@dataclass(frozen=True)
class RentalListingFilters:
    community_keyword: str | None
    resblock_ids: tuple[str, ...]
    listing_id: str | None
    scope: str
    monthly_rent_yuan: tuple[float | None, float | None] | None
    area_sqm: tuple[float | None, float | None] | None
    rooms: tuple[int, ...]
    orientations: tuple[str, ...]
    page: int
    page_size: int
    condition_filters: tuple[tuple[str, str | int | float], ...] = ()


@dataclass(frozen=True)
class RentalListingFilterOption:
    """One server-defined condition from the 房源列表 filter catalog."""

    key: str | None
    name: str
    value: str | int | float | None
    selection_type: str
    default_value: str | int | float | None
    children: tuple["RentalListingFilterOption", ...]


@dataclass(frozen=True)
class RentalListing:
    listing_id: str
    community: str
    layout: str | None
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str | None
    visible_scope: str
    # Upstream delType: 2 = 普租, 5 = 托管.  Only 普租 houses have a
    # detailHead record; callers should not attempt detail for 托管 ids.
    del_type: int | None = None


@dataclass(frozen=True)
class RentalListingPage:
    items: tuple[RentalListing, ...]
    page: int
    page_size: int
    has_more: bool
    request_id: str


@dataclass(frozen=True)
class MapBounds:
    min_longitude: float
    max_longitude: float
    min_latitude: float
    max_latitude: float


@dataclass(frozen=True)
class RentalMapSearchFilters:
    city_id: str
    data_source: str
    bounds: MapBounds
    page: int
    mode: str
    condition_tokens: tuple[str, ...]
    result_type: str | None
    resblock_id: str | None
    resblock_ids: tuple[str, ...]


@dataclass(frozen=True)
class RentalMapListing:
    listing_id: str
    title: str
    description: str
    tags: tuple[str, ...]
    price_text: str | None
    unit_price_text: str | None


@dataclass(frozen=True)
class RentalMapPage:
    items: tuple[RentalMapListing, ...]
    page: int
    total: int
    has_more: bool
    mode: str
    request_id: str


@dataclass(frozen=True)
class RentalMapBubbleFilters:
    city_id: str
    data_source: str
    bounds: MapBounds
    group_type: str
    group_id: str | None
    condition_tokens: tuple[str, ...]


@dataclass(frozen=True)
class RentalMapBubble:
    bubble_id: str
    name: str
    group_type: str
    latitude: float | None
    longitude: float | None
    count: int | None
    count_text: str | None
    price_text: str | None


@dataclass(frozen=True)
class RentalMapSuggestionFilters:
    city_id: str
    data_source: str
    query: str


@dataclass(frozen=True)
class RentalMapSuggestion:
    item_type: str
    item_type_name: str | None
    item_id: str
    name: str
    count_text: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class RentalMapNearbySearchFilters:
    """Human-oriented search centred on a resolved map location.

    The map upstream only accepts a collection of community identifiers for
    drawn-area searches.  ``radius_meters`` is therefore applied to community
    centroids by the application service before requesting the house list.
    """

    city_id: str
    data_source: str
    location: str
    center_latitude: float | None
    center_longitude: float | None
    radius_meters: int
    price_min_yuan: int | None
    price_max_yuan: int | None
    rooms: tuple[int, ...]
    rental_modes: tuple[str, ...]
    page: int


@dataclass(frozen=True)
class RentalMapNearbySearchResult:
    center: RentalMapSuggestion
    radius_meters: int
    matched_community_count: int
    result: RentalMapPage
