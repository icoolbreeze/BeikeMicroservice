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
    listing_id: str | None
    maintainer: str | None
    scope: str
    districts: tuple[str, ...]
    monthly_rent_yuan: tuple[float | None, float | None] | None
    area_sqm: tuple[float | None, float | None] | None
    rooms: tuple[int, ...]
    orientations: tuple[str, ...]
    tags: tuple[str, ...]
    page: int
    page_size: int


@dataclass(frozen=True)
class RentalListing:
    listing_id: str
    community: str
    layout: str | None
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str | None
    visible_scope: str


@dataclass(frozen=True)
class RentalListingPage:
    items: tuple[RentalListing, ...]
    page: int
    page_size: int
    has_more: bool
    request_id: str
