from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.application.rental_budget import price_range_for_budget
from app.domain.models import (
    ConnectionStatus,
    FollowRecord,
    HqiScore,
    ListingDetailInfo,
    ListingMaintainInfo,
    ListingProspect,
    ListingPropertyInfo,
    MaintainField,
    MaintainModule,
    Principal,
    ProspectPhoto,
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
    connection_state: str
    """Authorization state of the session provider (ready/expiring/...)."""
    credential_valid: bool
    """Whether a credential exists and is still within its validity period
    (ready or expiring, i.e. not auth_required/degraded)."""
    credential_expires_at: datetime | None
    """End of the active credential's validity period; null when the
    connector holds no credential."""
    checked_at: datetime


class ConnectionStatusResponse(BaseModel):
    state: str
    message: str
    bound_employee_principal: str | None
    mcp_transport: str
    checked_at: datetime
    credential_expires_at: datetime | None = None

    @classmethod
    def from_domain(cls, status: ConnectionStatus) -> "ConnectionStatusResponse":
        return cls(
            state=status.state.value,
            message=status.message,
            bound_employee_principal=status.bound_employee_principal,
            mcp_transport=status.mcp_transport,
            checked_at=status.checked_at,
            credential_expires_at=status.credential_expires_at,
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
    # Detail-only fields; None for search-page rows (see §房源详情).
    maintain_org: str | None = None
    source: str | None = None
    floor_desc: str | None = None
    total_floors: int | None = None
    listed_days: int | None = None
    house_grade: str | None = None
    follow_total: int | None = None
    follow_last_7d: int | None = None
    showing_total: int | None = None
    showing_last_7d: int | None = None
    external_url_ke: str | None = None
    external_url_lianjia: str | None = None
    has_key: bool | None = None
    del_status_text: str | None = None
    house_id: str | None = None

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


class ProspectPhotoResponse(BaseModel):
    url: str
    room_name: str | None = None
    image_type: str
    """Upstream imageType: REAL = 实勘图, TITLE = 标题图."""
    upload_user: str | None = None
    created_at: datetime | None = None

    @classmethod
    def from_domain(cls, photo: ProspectPhoto) -> "ProspectPhotoResponse":
        return cls(**photo.__dict__)


class ListingProspectResponse(BaseModel):
    listing_id: str
    photos: list[ProspectPhotoResponse] = Field(default_factory=list)
    floor_plan_url: str | None = None
    can_edit: bool | None = None
    has_survey_photo: bool
    """True when at least one REAL (实勘) photo has been uploaded."""

    @classmethod
    def from_domain(cls, prospect: ListingProspect) -> "ListingProspectResponse":
        return cls(
            listing_id=prospect.listing_id,
            photos=[ProspectPhotoResponse.from_domain(photo) for photo in prospect.photos],
            floor_plan_url=prospect.floor_plan_url,
            can_edit=prospect.can_edit,
            has_survey_photo=prospect.has_survey_photo,
        )


class ListingPropertyInfoResponse(BaseModel):
    # 小区信息
    listing_id: str
    community: str | None = None
    district: str | None = None
    biz_circle: str | None = None
    tenement_fee: str | None = None
    kindergarten: str | None = None
    # 建筑信息
    building_type: str | None = None
    building_structure: str | None = None
    building_year: int | None = None
    property_purpose: str | None = None
    deal_property: str | None = None
    age_limit: str | None = None
    disgust_desc: str | None = None
    haunted_desc: str | None = None
    # 生活信息
    elevator: str | None = None
    ti_hu_ratio: str | None = None
    water_type: str | None = None
    electric_type: str | None = None
    heating: str | None = None
    heating_fee: str | None = None
    gas: str | None = None
    gas_fee: str | None = None
    hot_water: str | None = None
    hot_water_fee: str | None = None
    middle_water: str | None = None
    middle_water_fee: str | None = None
    parking_ratio: str | None = None
    parking_fee: str | None = None
    parking_above_ground: str | None = None
    parking_underground: str | None = None
    green_rate: float | None = None
    cubage_rate: float | None = None

    @classmethod
    def from_domain(cls, info: ListingPropertyInfo) -> "ListingPropertyInfoResponse":
        return cls(**info.__dict__)


class HqiHeatItemResponse(BaseModel):
    name: str
    value: str | None = None
    fluctuate: str | None = None
    positive: bool | None = None


class HqiSuggestionResponse(BaseModel):
    item: str | None = None
    suggestion: str | None = None


class HqiScoreResponse(BaseModel):
    total_score: str | None = None
    level: str | None = None
    next_level: str | None = None
    rank_text: str | None = None
    pending_optimize: str | None = None
    heat_items: list[HqiHeatItemResponse] = Field(default_factory=list)
    suggestions: list[HqiSuggestionResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, score: HqiScore) -> "HqiScoreResponse":
        return cls(
            total_score=score.total_score,
            level=score.level,
            next_level=score.next_level,
            rank_text=score.rank_text,
            pending_optimize=score.pending_optimize,
            heat_items=[
                HqiHeatItemResponse(
                    name=item.name,
                    value=item.value,
                    fluctuate=item.fluctuate,
                    positive=item.positive,
                )
                for item in score.heat_items
            ],
            suggestions=[
                HqiSuggestionResponse(item=suggestion.item, suggestion=suggestion.suggestion)
                for suggestion in score.suggestions
            ],
        )


class MaintainFieldResponse(BaseModel):
    name: str
    display_value: str | None = None
    complete: bool | None = None


class MaintainModuleResponse(BaseModel):
    rate_text: str | None = None
    fields: list[MaintainFieldResponse] = Field(default_factory=list)


class ListingMaintainInfoResponse(BaseModel):
    listing_id: str
    modules: list[MaintainModuleResponse] = Field(default_factory=list)
    remark: str | None = None
    all_field_rate: int | None = None
    important_rate: int | None = None
    owner_lowest_price: str | None = None


class FollowRecordResponse(BaseModel):
    content: str
    follow_type: str | None = None
    creator_name: str | None = None
    role: str | None = None
    created_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    label_code: str | None = None
    remarks: str | None = None
    on_top: bool = False
    on_top_time: datetime | None = None


class ListingDetailInfoResponse(BaseModel):
    listing_id: str
    labels: list[str] = Field(default_factory=list)
    property_info: ListingPropertyInfoResponse | None = None
    hqi: HqiScoreResponse | None = None
    maintain: ListingMaintainInfoResponse | None = None
    follows: list[FollowRecordResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, info: ListingDetailInfo) -> "ListingDetailInfoResponse":
        return cls(
            listing_id=info.listing_id,
            labels=list(info.labels),
            property_info=(
                ListingPropertyInfoResponse.from_domain(info.property_info)
                if info.property_info is not None
                else None
            ),
            hqi=HqiScoreResponse.from_domain(info.hqi) if info.hqi is not None else None,
            maintain=(
                ListingMaintainInfoResponse(
                    listing_id=info.maintain.listing_id,
                    modules=[
                        MaintainModuleResponse(
                            rate_text=module.rate_text,
                            fields=[
                                MaintainFieldResponse(
                                    name=field.name,
                                    display_value=field.display_value,
                                    complete=field.complete,
                                )
                                for field in module.fields
                            ],
                        )
                        for module in info.maintain.modules
                    ],
                    remark=info.maintain.remark,
                    all_field_rate=info.maintain.all_field_rate,
                    important_rate=info.maintain.important_rate,
                    owner_lowest_price=info.maintain.owner_lowest_price,
                )
                if info.maintain is not None
                else None
            ),
            follows=[
                FollowRecordResponse(
                    content=record.content,
                    follow_type=record.follow_type,
                    creator_name=record.creator_name,
                    role=record.role,
                    created_at=record.created_at,
                    labels=list(record.labels),
                    label_code=record.label_code,
                    remarks=record.remarks,
                    on_top=record.on_top,
                    on_top_time=record.on_top_time,
                )
                for record in info.follows
            ],
        )


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
    community_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Community ids whose centroids fall inside the radius circle. "
            "Pass as resblock_ids to rental_listing_search to continue filtering "
            "the same circle with the full listing-filter catalog."
        ),
    )
    result: RentalMapPageResponse
    approximation: Literal["community_centroid"] = "community_centroid"

    @classmethod
    def from_domain(cls, result: RentalMapNearbySearchResult) -> "RentalMapNearbySearchResponse":
        return cls(
            center=RentalMapSuggestionResponse.from_domain(result.center),
            radius_meters=result.radius_meters,
            matched_community_count=result.matched_community_count,
            community_ids=list(result.community_ids),
            result=RentalMapPageResponse.from_domain(result.result),
        )
