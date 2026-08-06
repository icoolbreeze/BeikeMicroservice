from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import (
    ConnectionStatus,
    Principal,
    RentalListing,
    RentalListingFilters,
    RentalListingPage,
)
from app.domain.modules import CrmModule


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["crm_connector"]


class ConnectionStatusResponse(BaseModel):
    state: str
    message: str
    bound_employee_principal: str | None
    mcp_transport: str
    checked_at: datetime

    @classmethod
    def from_domain(cls, status: ConnectionStatus) -> "ConnectionStatusResponse":
        return cls(
            state=status.state.value,
            message=status.message,
            bound_employee_principal=status.bound_employee_principal,
            mcp_transport=status.mcp_transport,
            checked_at=status.checked_at,
        )


class PrincipalResponse(BaseModel):
    employee_principal: str
    display_name: str | None = None

    @classmethod
    def from_domain(cls, principal: Principal) -> "PrincipalResponse":
        return cls(
            employee_principal=principal.employee_principal, display_name=principal.display_name
        )


class ModuleResponse(BaseModel):
    module_id: str
    name: str
    parent_id: str | None
    status: str
    note: str

    @classmethod
    def from_domain(cls, module: CrmModule) -> "ModuleResponse":
        return cls(
            module_id=module.module_id,
            name=module.name,
            parent_id=module.parent_id,
            status=module.status.value,
            note=module.note,
        )


class NumericRange(BaseModel):
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)


class RentalListingSearchRequest(BaseModel):
    community_keyword: str | None = Field(default=None, max_length=128)
    listing_id: str | None = Field(default=None, max_length=64)
    maintainer: str | None = Field(default=None, max_length=128)
    scope: Literal["my_maintained", "shared", "role_visible"] = "my_maintained"
    districts: list[str] = Field(default_factory=list, max_length=20)
    monthly_rent_yuan: NumericRange | None = None
    area_sqm: NumericRange | None = None
    rooms: list[int] = Field(default_factory=list, max_length=8)
    orientations: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=50)

    def to_domain(self) -> RentalListingFilters:
        return RentalListingFilters(
            community_keyword=self.community_keyword,
            listing_id=self.listing_id,
            maintainer=self.maintainer,
            scope=self.scope,
            districts=tuple(self.districts),
            monthly_rent_yuan=(
                (self.monthly_rent_yuan.min, self.monthly_rent_yuan.max)
                if self.monthly_rent_yuan
                else None
            ),
            area_sqm=(self.area_sqm.min, self.area_sqm.max) if self.area_sqm else None,
            rooms=tuple(self.rooms),
            orientations=tuple(self.orientations),
            tags=tuple(self.tags),
            page=self.page,
            page_size=self.page_size,
        )


class RentalListingResponse(BaseModel):
    listing_id: str
    community: str
    layout: str | None = None
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    orientation: str | None = None
    visible_scope: str

    @classmethod
    def from_domain(cls, listing: RentalListing) -> "RentalListingResponse":
        return cls(**listing.__dict__)


class RentalListingPageResponse(BaseModel):
    items: list[RentalListingResponse]
    page: int
    page_size: int
    has_more: bool
    request_id: str

    @classmethod
    def from_domain(cls, result: RentalListingPage) -> "RentalListingPageResponse":
        return cls(
            items=[RentalListingResponse.from_domain(item) for item in result.items],
            page=result.page,
            page_size=result.page_size,
            has_more=result.has_more,
            request_id=result.request_id,
        )
