from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.application.rental_budget import price_range_for_budget
from app.domain.models import (
    ConnectionStatus,
    Principal,
    RentalListing,
    RentalListingFilterOption,
    RentalListingFilters,
    RentalListingPage,
    MapBounds,
    RentalMapBubble,
    RentalMapBubbleFilters,
    RentalMapListing,
    RentalMapNearbySearchFilters,
    RentalMapNearbySearchResult,
    RentalMapPage,
    RentalMapSearchFilters,
    RentalMapSuggestion,
    RentalMapSuggestionFilters,
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


class QrLoginStartResponse(BaseModel):
    login_id: str
    state: str
    qrcode: str
    note: str = ""
    message: str = ""


class QrLoginStatusResponse(BaseModel):
    login_id: str
    state: str
    qrcode: str
    note: str = ""
    message: str = ""
    employee_principal: str | None = None
    expires_at: datetime | None = None

    @classmethod
    def from_status(cls, status) -> "QrLoginStatusResponse":
        return cls(
            login_id=status.login_id,
            state=status.state,
            qrcode=status.qrcode,
            note=status.note,
            message=status.message,
            employee_principal=status.employee_principal,
            expires_at=status.expires_at,
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


MapConditionToken = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9]*$",
        description="A known map condition token, for example l2, a3, ditie1, or orp2.",
    ),
]

RentalMode = Literal["whole_rent", "shared_rent"]

ListingConditionValue = str | int | float | list[str | int | float]

# Keys observed in the live 房源列表 ``searchOption`` catalog.  The catalog is
# exposed through ``rental_listing_filter_options`` so callers never need to
# guess values, while this allow-list prevents the list endpoint becoming an
# arbitrary query proxy.
_LISTING_CONDITION_KEYS = frozenset({
    "area", "bathroomAmount", "bedroomAmount", "bizCircleId", "buildingType",
    "delSourceType", "delType", "districtId", "entryTime", "externalAppearance",
    "fitment", "floor", "houseAge", "houseCurrentStatus", "houseGrade",
    "houseUsage", "hqiPictureScore", "hqiScoreRangeType", "key", "label",
    "layoutType", "orientation", "ownerWeChatStatus", "paymentMode",
    "paymentType", "preserveHouse", "price", "prospect", "relationRange",
    "rentPeriod", "rentSell", "rentType", "role", "shengXinZuLabel",
    "toiletType", "trusteeshipNegotiateStatus", "visitTime", "vrStatus",
})


def _listing_condition_filters(
    filters: dict[str, ListingConditionValue],
) -> tuple[tuple[str, str | int | float], ...]:
    unknown = sorted(set(filters) - _LISTING_CONDITION_KEYS)
    if unknown:
        raise ValueError(f"unsupported listing condition filter keys: {', '.join(unknown)}")
    normalized: list[tuple[str, str | int | float]] = []
    for key, value in filters.items():
        if isinstance(value, list):
            if not value or len(value) > 50:
                raise ValueError(f"condition_filters.{key} must contain 1..50 values")
            normalized.append((key, ",".join(str(item) for item in value)))
        elif isinstance(value, str):
            if not value:
                raise ValueError(f"condition_filters.{key} must not be empty")
            normalized.append((key, value))
        else:
            normalized.append((key, value))
    return tuple(normalized)


def _map_filter_tokens(
    condition_tokens: list[str], rooms: list[int], rental_modes: list[RentalMode]
) -> tuple[str, ...]:
    """Combine advanced and semantic map filters in the UI's token format."""
    semantic_tokens = [f"l{room}" for room in sorted(set(rooms))]
    semantic_tokens.extend(
        {"whole_rent": "rt001", "shared_rent": "rt002"}[mode]
        for mode in rental_modes
    )
    return tuple(dict.fromkeys([*condition_tokens, *semantic_tokens]))


class MapBoundsRequest(BaseModel):
    min_longitude: float = Field(ge=-180, le=180)
    max_longitude: float = Field(ge=-180, le=180)
    min_latitude: float = Field(ge=-90, le=90)
    max_latitude: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def validate_order(self) -> "MapBoundsRequest":
        if self.min_longitude >= self.max_longitude:
            raise ValueError("min_longitude must be less than max_longitude")
        if self.min_latitude >= self.max_latitude:
            raise ValueError("min_latitude must be less than max_latitude")
        return self

    def to_domain(self) -> MapBounds:
        return MapBounds(
            min_longitude=self.min_longitude,
            max_longitude=self.max_longitude,
            min_latitude=self.min_latitude,
            max_latitude=self.max_latitude,
        )


class RentalMapSearchRequest(BaseModel):
    city_id: str | None = Field(default=None, max_length=32)
    data_source: Literal["ZF"] = "ZF"
    mode: Literal["viewport", "circle"] = "viewport"
    bounds: MapBoundsRequest
    page: int = Field(default=1, ge=1, le=1000)
    condition_tokens: list[MapConditionToken] = Field(default_factory=list, max_length=100)
    rooms: list[int] = Field(
        default_factory=list,
        max_length=5,
        description="Bedroom counts: 1 through 5, where 5 means five bedrooms or more.",
    )
    rental_modes: list[RentalMode] = Field(
        default_factory=list,
        max_length=2,
        description="Rental modes: whole_rent (整租) and shared_rent (合租).",
    )
    result_type: str | None = Field(default=None, max_length=32)
    resblock_id: str | None = Field(default=None, max_length=64)
    resblock_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_mode(self) -> "RentalMapSearchRequest":
        if self.mode == "circle" and not self.resblock_ids:
            raise ValueError("resblock_ids is required when mode is circle")
        if any(room < 1 or room > 5 for room in self.rooms):
            raise ValueError("rooms values must be in the range 1..5")
        return self

    def to_domain(self, default_city_id: str) -> RentalMapSearchFilters:
        return RentalMapSearchFilters(
            city_id=self.city_id or default_city_id,
            data_source=self.data_source,
            bounds=self.bounds.to_domain(),
            page=self.page,
            mode=self.mode,
            condition_tokens=_map_filter_tokens(
                self.condition_tokens, self.rooms, self.rental_modes
            ),
            result_type=self.result_type,
            resblock_id=self.resblock_id,
            resblock_ids=tuple(self.resblock_ids),
        )


class RentalMapBubbleRequest(BaseModel):
    city_id: str | None = Field(default=None, max_length=32)
    data_source: Literal["ZF"] = "ZF"
    bounds: MapBoundsRequest
    group_type: Literal["district", "bizcircle", "community"]
    group_id: str | None = Field(default=None, max_length=64)
    condition_tokens: list[MapConditionToken] = Field(default_factory=list, max_length=100)
    rooms: list[int] = Field(default_factory=list, max_length=5)
    rental_modes: list[RentalMode] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def validate_rooms(self) -> "RentalMapBubbleRequest":
        if any(room < 1 or room > 5 for room in self.rooms):
            raise ValueError("rooms values must be in the range 1..5")
        return self

    def to_domain(self, default_city_id: str) -> RentalMapBubbleFilters:
        return RentalMapBubbleFilters(
            city_id=self.city_id or default_city_id,
            data_source=self.data_source,
            bounds=self.bounds.to_domain(),
            group_type=self.group_type,
            group_id=self.group_id,
            condition_tokens=_map_filter_tokens(
                self.condition_tokens, self.rooms, self.rental_modes
            ),
        )


class RentalMapSuggestionRequest(BaseModel):
    city_id: str | None = Field(default=None, max_length=32)
    data_source: Literal["ZF"] = "ZF"
    query: str = Field(min_length=1, max_length=128)

    def to_domain(self, default_city_id: str) -> RentalMapSuggestionFilters:
        return RentalMapSuggestionFilters(
            city_id=self.city_id or default_city_id,
            data_source=self.data_source,
            query=self.query,
        )


class RentalMapNearbySearchRequest(BaseModel):
    """Semantic map search for queries such as a two-bedroom near a landmark."""

    city_id: str | None = Field(default=None, max_length=32)
    data_source: Literal["ZF"] = "ZF"
    location: str = Field(min_length=1, max_length=128)
    center_latitude: float | None = Field(default=None, ge=-90, le=90)
    center_longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: int = Field(default=1000, ge=100, le=5000)
    price_min_yuan: int | None = Field(
        default=None,
        ge=0,
        le=100000,
        description=(
            "Optional lower monthly-rent bound. For shared_rent, omit this by default "
            "to prioritize affordability; provide it only when the user explicitly requests one."
        ),
    )
    price_max_yuan: int | None = Field(default=None, ge=0, le=100000)
    rooms: list[int] = Field(default_factory=list, max_length=5)
    rental_modes: list[RentalMode] = Field(
        default_factory=list,
        max_length=2,
        description="Rental modes: whole_rent (整租) and shared_rent (合租).",
    )
    page: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_ranges(self) -> "RentalMapNearbySearchRequest":
        if (
            self.price_min_yuan is not None
            and self.price_max_yuan is not None
            and self.price_min_yuan > self.price_max_yuan
        ):
            raise ValueError("price_min_yuan must not exceed price_max_yuan")
        if any(room < 1 or room > 5 for room in self.rooms):
            raise ValueError("rooms values must be in the range 1..5")
        if (self.center_latitude is None) != (self.center_longitude is None):
            raise ValueError("center_latitude and center_longitude must be supplied together")
        return self

    def to_domain(self, default_city_id: str) -> RentalMapNearbySearchFilters:
        return RentalMapNearbySearchFilters(
            city_id=self.city_id or default_city_id,
            data_source=self.data_source,
            location=self.location,
            center_latitude=self.center_latitude,
            center_longitude=self.center_longitude,
            radius_meters=self.radius_meters,
            price_min_yuan=self.price_min_yuan,
            price_max_yuan=self.price_max_yuan,
            rooms=tuple(self.rooms),
            rental_modes=tuple(self.rental_modes),
            page=self.page,
        )


class RentalListingSearchRequest(BaseModel):
    resblock_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Exact community identifiers. Obtain these from rental_map_suggest "
            "by selecting results whose item_type is resblock. Supports multiple "
            "communities and maps to the page-native resblockId filter."
        ),
    )
    community_keyword: str | None = Field(default=None, max_length=128)
    listing_id: str | None = Field(default=None, max_length=64)
    scope: Literal["my_maintained", "shared", "role_visible"] = "my_maintained"
    monthly_rent_yuan: NumericRange | None = None
    area_sqm: NumericRange | None = None
    rooms: list[int] = Field(default_factory=list, max_length=8)
    orientations: list[str] = Field(default_factory=list, max_length=10)
    condition_filters: dict[str, ListingConditionValue] = Field(
        default_factory=dict,
        max_length=37,
        description=(
            "Page-native filters. Retrieve valid keys and values first with "
            "rental_listing_filter_options; only the current 房源列表 catalog keys are accepted."
        ),
    )
    budget_yuan: int | None = Field(
        default=None,
        ge=100,
        le=100000,
        description=(
            "Optional customer monthly budget. Generates price=[budget/2, budget + "
            "clamp(25% of budget, 200, 500)]; when rentType=002 (合租), lower price is 0."
        ),
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def validate_condition_filters(self) -> "RentalListingSearchRequest":
        _listing_condition_filters(self.condition_filters)
        if any(not item.strip() for item in self.resblock_ids):
            raise ValueError("resblock_ids cannot contain blank values")
        if self.community_keyword and self.resblock_ids:
            raise ValueError(
                "community_keyword cannot be combined with exact resblock_ids"
            )
        if self.budget_yuan is not None and "price" in self.condition_filters:
            raise ValueError("budget_yuan cannot be combined with condition_filters.price")
        return self

    def to_domain(self) -> RentalListingFilters:
        condition_filters = dict(_listing_condition_filters(self.condition_filters))
        if self.budget_yuan is not None:
            minimum, maximum = price_range_for_budget(
                self.budget_yuan,
                shared_rent=condition_filters.get("rentType") == "002",
            )
            condition_filters["price"] = f"{minimum}:{maximum}"
        return RentalListingFilters(
            community_keyword=self.community_keyword,
            resblock_ids=tuple(dict.fromkeys(self.resblock_ids)),
            listing_id=self.listing_id,
            scope=self.scope,
            monthly_rent_yuan=(
                (self.monthly_rent_yuan.min, self.monthly_rent_yuan.max)
                if self.monthly_rent_yuan
                else None
            ),
            area_sqm=(self.area_sqm.min, self.area_sqm.max) if self.area_sqm else None,
            rooms=tuple(self.rooms),
            orientations=tuple(self.orientations),
            page=self.page,
            page_size=self.page_size,
            condition_filters=tuple(condition_filters.items()),
        )


class RentalListingResponse(BaseModel):
    listing_id: str
    community: str
    layout: str | None = None
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    orientation: str | None = None
    visible_scope: str
    del_type: int | None = None

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


class RentalListingFilterOptionResponse(BaseModel):
    key: str | None = None
    name: str
    value: str | int | float | None = None
    selection_type: str
    default_value: str | int | float | None = None
    children: list["RentalListingFilterOptionResponse"] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls, option: RentalListingFilterOption
    ) -> "RentalListingFilterOptionResponse":
        return cls(
            key=option.key,
            name=option.name,
            value=option.value,
            selection_type=option.selection_type,
            default_value=option.default_value,
            children=[cls.from_domain(child) for child in option.children],
        )


RentalListingFilterOptionResponse.model_rebuild()


class RentalMapListingResponse(BaseModel):
    listing_id: str
    title: str
    description: str
    tags: list[str]
    price_text: str | None = None
    unit_price_text: str | None = None

    @classmethod
    def from_domain(cls, listing: RentalMapListing) -> "RentalMapListingResponse":
        return cls(
            listing_id=listing.listing_id,
            title=listing.title,
            description=listing.description,
            tags=list(listing.tags),
            price_text=listing.price_text,
            unit_price_text=listing.unit_price_text,
        )


class RentalMapPageResponse(BaseModel):
    items: list[RentalMapListingResponse]
    page: int
    total: int
    has_more: bool
    mode: str
    request_id: str

    @classmethod
    def from_domain(cls, result: RentalMapPage) -> "RentalMapPageResponse":
        return cls(
            items=[RentalMapListingResponse.from_domain(item) for item in result.items],
            page=result.page,
            total=result.total,
            has_more=result.has_more,
            mode=result.mode,
            request_id=result.request_id,
        )


class RentalMapBubbleResponse(BaseModel):
    bubble_id: str
    name: str
    group_type: str
    latitude: float | None = None
    longitude: float | None = None
    count: int | None = None
    count_text: str | None = None
    price_text: str | None = None

    @classmethod
    def from_domain(cls, bubble: RentalMapBubble) -> "RentalMapBubbleResponse":
        return cls(**bubble.__dict__)


class RentalMapSuggestionResponse(BaseModel):
    item_type: str
    item_type_name: str | None = None
    item_id: str
    name: str
    count_text: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @classmethod
    def from_domain(cls, suggestion: RentalMapSuggestion) -> "RentalMapSuggestionResponse":
        return cls(**suggestion.__dict__)


class RentalMapNearbySearchResponse(BaseModel):
    center: RentalMapSuggestionResponse
    radius_meters: int
    matched_community_count: int
    result: RentalMapPageResponse
    approximation: Literal["community_centroid"] = "community_centroid"

    @classmethod
    def from_domain(cls, result: RentalMapNearbySearchResult) -> "RentalMapNearbySearchResponse":
        return cls(
            center=RentalMapSuggestionResponse.from_domain(result.center),
            radius_meters=result.radius_meters,
            matched_community_count=result.matched_community_count,
            result=RentalMapPageResponse.from_domain(result.result),
        )
