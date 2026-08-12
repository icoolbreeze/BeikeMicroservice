from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.application.rental_budget import price_range_for_budget
from app.domain.models import (
    ConnectionStatus,
    HqiScore,
    ListingDetailInfo,
    ListingProspect,
    ListingPropertyInfo,
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
    SaleCommunitySuggestion,
    SaleFollowRecord,
    SaleListing,
    SaleListingDetail,
    SaleListingFilters,
    SaleListingFilterOption,
    SaleListingPage,
    SaleMaintainInfo,
    SaleMapNearbySearchFilters,
    SaleMapNearbySearchResult,
    SaleMapSuggestion,
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
    # ``all`` is the CRM page's "不限" option (relationRange=0).  Keep the
    # historical maintenance-pool default for general API callers; featured
    # collection opts into ``all`` explicitly.
    scope: Literal["all", "my_maintained", "shared", "role_visible"] = "my_maintained"
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
    # Raw img.ljcdn.com originals (titleImage / floorPlanImage). Direct fetch
    # returns 403; append a size suffix (.450x/.750x/.800x/.1500x.jpg) to get
    # a public variant (docs/rental-image-cdn.md). None for detail responses.
    title_image_url: str | None = None
    floor_plan_image_url: str | None = None

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


# ---------------------------------------------------------------------------
# 买卖 (sale) — house.link.lianjia.com. Request/response models mirror the
# live workbench contract (docs/sale-api-catalog.md).
# ---------------------------------------------------------------------------

# 范围 (vertical) ids from the getSearchFilters catalog.
SALE_SCOPE = Literal[
    "all",
    "gdiv_mt",               # 维护盘房源
    "gdiv_share",            # 共享盘房源
    "gdiv_score_division",   # 积分盘房源
    "share_pool_org",        # 店共享池房源
    "jmgroup_pool",          # 店东共享池房源
    "acn_pool",              # 维护盘共享池房源
    "follow_housenew",       # 关注房源
    "rolenew",               # 角色房源
]

# 筛选 dropdown keys from the catalog's select group (appro_broker 实勘,
# key_broker 钥匙, role 我的角色, house_stat 房屋现状, credential_type 证件状态,
# isParkingPlace 车位, statFunction 房屋用途, del_grade 房屋等级,
# fitment_status 装修, bathroom_n 卫生间数, appearance 外网呈现,
# houseSpread 推广房源, isElevatorHouse 电梯, frameStructureFilter 户型结构,
# beautifyHouse 美化房, haveOwnerReservePrice 业主预期价, hasSmartLock 智能门锁).
_SALE_SELECT_KEYS = frozenset({
    "appro_broker", "key_broker", "role", "house_stat", "credential_type",
    "isParkingPlace", "statFunction", "del_grade", "fitment_status",
    "bathroom_n", "appearance", "houseSpread", "isElevatorHouse",
    "frameStructureFilter", "beautifyHouse", "haveOwnerReservePrice",
    "hasSmartLock",
})

_SALE_SORT_VALUES = frozenset({
    "period1_desc_createtime_desc",  # 默认（新上优先）
    "period1_asc_totalprice",
    "period1_desc_totalprice",
})


def _sale_select_filters(
    select: dict[str, str | int],
) -> tuple[tuple[str, str], ...]:
    unknown = sorted(set(select) - _SALE_SELECT_KEYS)
    if unknown:
        raise ValueError(f"unsupported sale select filter keys: {', '.join(unknown)}")
    normalized: list[tuple[str, str]] = []
    for key, value in select.items():
        normalized.append((key, str(value)))
    return tuple(normalized)


class SaleListingSearchRequest(BaseModel):
    """买卖 全部房源 search. Filter values come from the sale filter catalog
    (sale_listing_filter_options); community ids from sale_community_suggest."""

    scope: SALE_SCOPE = "gdiv_mt"
    community_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Exact community identifiers from sale_community_suggest; maps to "
            "the multi_community_id filter. Supports multiple communities."
        ),
    )
    listing_id: str | None = Field(
        default=None,
        max_length=64,
        description="Exact 房源编号 (del_code) to look up one listing.",
    )
    district_id: str | None = Field(
        default=None,
        max_length=32,
        description="商圈 (disId) from the filter catalog; -1 means 不限.",
    )
    total_price_wan: NumericRange | None = Field(
        default=None,
        description="总价区间（万元），例如 50..70 对应 50-70万。",
    )
    area_sqm: NumericRange | None = Field(
        default=None,
        description="建筑面积区间（平米），例如 70..110。",
    )
    rooms: list[int] = Field(
        default_factory=list,
        max_length=8,
        description="房型室数（1..5，5 表示 5 室以上）；多值按 min..max 区间发送。",
    )
    floors: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "楼层条件（floorNew）：under_ground 地下室 / first_floor 一层 / "
            "top_floor 顶层 / not_under_ground 不看地下室 / not_first_floor "
            "不看一层 / not_top_floor 不看顶层。"
        ),
    )
    orientations: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "朝向（orient）编码：100500000001 东 … 100500000008 东北；"
            "100500000003;100500000007 南北组合。"
        ),
    )
    house_layouts: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "户型（houseLayout）：withTerrace 带露台 / withCourtyard 带小院 / "
            "withAttic 带阁楼 / brightBathroom 明卫 / northSouthTransparent "
            "南北通透 / bedroomFacesSouth 卧室朝南。"
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "标签（tag）：has_subway 地铁房 / mwwy 满五唯一 / mw 满五 / me 满二 / "
            "bikan_haofang 必看好房 / rent_sale 租售 / vrStatus VR房 / "
            "is_school 附近有学校 / buy_limit 不限购 / is_elevator_house 电梯房 / "
            "new_house_in_7_days 新上房源 …"
        ),
    )
    house_age: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="建成年代（h_age）：0 两年内 … 6 三十年以上。",
    )
    visitable_times: int | None = Field(
        default=None,
        ge=1,
        le=4,
        description="可看时间（visitable_times）：1 今天可看 … 4 随时可看。",
    )
    payment_mode: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "交易权属（payment_mode）：307500000001 商品房 / 307500000002 "
            "已购公房 / 307500000016 私产 …"
        ),
    )
    building_type: str | None = Field(
        default=None,
        max_length=32,
        description="建筑类型（b_type）：102200000001 塔楼 / …0002 板楼 / …",
    )
    select: dict[str, str | int] = Field(
        default_factory=dict,
        max_length=17,
        description=(
            "筛选 dropdown（key=value）：appro_broker 实勘 / key_broker 钥匙 / "
            "role 我的角色 / house_stat 房屋现状 / credential_type 证件状态 / "
            "isParkingPlace 车位 / statFunction 房屋用途 / del_grade 房屋等级 / "
            "fitment_status 装修 / bathroom_n 卫生间数 / appearance 外网呈现 / "
            "houseSpread 推广房源 / isElevatorHouse 电梯 / frameStructureFilter "
            "户型结构 / beautifyHouse 美化房 / haveOwnerReservePrice 业主预期价。"
            "value 取 catalog 枚举 id；-1（不限）会被省略。"
        ),
    )
    sort: str = Field(
        default="period1_desc_createtime_desc",
        pattern=r"^(period1_desc_createtime_desc|period1_asc_totalprice|period1_desc_totalprice)$",
        description="排序：period1_desc_createtime_desc 新上优先（默认）/ "
        "period1_asc_totalprice 总价升序 / period1_desc_totalprice 总价降序。",
    )
    page: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_sale_filters(self) -> "SaleListingSearchRequest":
        _sale_select_filters(self.select)
        if any(not item.strip() for item in self.community_ids):
            raise ValueError("community_ids cannot contain blank values")
        if self.listing_id and self.community_ids:
            raise ValueError("listing_id cannot be combined with community_ids")
        if any(room < 1 or room > 5 for room in self.rooms):
            raise ValueError("rooms values must be in the range 1..5")
        return self

    def to_domain(self) -> SaleListingFilters:
        return SaleListingFilters(
            scope=self.scope,
            community_ids=tuple(dict.fromkeys(self.community_ids)),
            district_id=None if self.district_id in (None, "-1") else self.district_id,
            listing_id=self.listing_id,
            price_wan=(
                (self.total_price_wan.min, self.total_price_wan.max)
                if self.total_price_wan
                else None
            ),
            area_sqm=(self.area_sqm.min, self.area_sqm.max) if self.area_sqm else None,
            rooms=tuple(sorted(set(self.rooms))),
            floors=tuple(dict.fromkeys(self.floors)),
            orientations=tuple(dict.fromkeys(self.orientations)),
            house_layouts=tuple(dict.fromkeys(self.house_layouts)),
            tags=tuple(dict.fromkeys(self.tags)),
            select=_sale_select_filters(self.select),
            house_age=self.house_age,
            visitable_times=self.visitable_times,
            payment_mode=self.payment_mode,
            building_type=self.building_type,
            sort=self.sort,
            page=self.page,
        )


class SaleListingResponse(BaseModel):
    listing_id: str
    community: str
    biz_circle: str | None = None
    layout: str | None = None
    area_sqm: float | None = None
    total_price_yuan: float | None = None
    total_price_text: str | None = None
    unit_price_yuan_per_sqm: float | None = None
    floor_desc: str | None = None
    floor_type: str | None = None
    orientation: str | None = None
    tags: list[str] = Field(default_factory=list)
    visit_count_15d: int | None = None
    follow_up: bool | None = None
    create_time: datetime | None = None
    maintainer_name: str | None = None
    maintainer_tag: str | None = None
    maintain_percentage: int | None = None
    quality_score: float | None = None
    holder_level: str | None = None
    del_type: int | None = None
    community_id: str | None = None
    payment_mode: str | None = None
    stat_function: str | None = None
    subway_line: str | None = None
    subway_station: str | None = None
    vr_status: int | None = None
    surface_image_url: str | None = None
    floor_plan_image_url: str | None = None

    @classmethod
    def from_domain(cls, listing: SaleListing) -> "SaleListingResponse":
        return cls(**listing.__dict__)


class SaleListingPageResponse(BaseModel):
    items: list[SaleListingResponse]
    page: int
    total: int
    has_more: bool
    request_id: str

    @classmethod
    def from_domain(cls, result: SaleListingPage) -> "SaleListingPageResponse":
        return cls(
            items=[SaleListingResponse.from_domain(item) for item in result.items],
            page=result.page,
            total=result.total,
            has_more=result.has_more,
            request_id=result.request_id,
        )


class SaleListingFilterOptionResponse(BaseModel):
    key: str | None = None
    name: str
    value: str | None = None
    selection_type: str = ""
    default_value: str | None = None
    for_show: bool = False
    ext: dict[str, object] = Field(default_factory=dict)
    children: list["SaleListingFilterOptionResponse"] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls, option: SaleListingFilterOption
    ) -> "SaleListingFilterOptionResponse":
        return cls(
            key=option.key,
            name=option.name,
            value=option.value,
            selection_type=option.selection_type,
            default_value=option.default_value,
            for_show=option.for_show,
            ext=option.ext,
            children=[cls.from_domain(child) for child in option.children],
        )


SaleListingFilterOptionResponse.model_rebuild()


class SaleCommunitySuggestionResponse(BaseModel):
    text: str
    community_id: str
    resblock_name: str | None = None
    resblock_alias: str | None = None
    district_name: str | None = None
    bizcircle_name: str | None = None
    house_count: int | None = None
    del_type: str | None = None

    @classmethod
    def from_domain(
        cls, suggestion: SaleCommunitySuggestion
    ) -> "SaleCommunitySuggestionResponse":
        return cls(**suggestion.__dict__)


class SaleListingDetailResponse(BaseModel):
    listing_id: str
    display_name: str | None = None
    display_price: str | None = None
    latest_price_yuan: float | None = None
    unit_price_text: str | None = None
    area_sqm: float | None = None
    bedroom_amount: int | None = None
    parlor_amount: int | None = None
    toilet_amount: int | None = None
    cookroom_amount: int | None = None
    display_floor: str | None = None
    orientation: str | None = None
    del_grade: str | None = None
    broker_grade: str | None = None
    holder_name: str | None = None
    holder_org: str | None = None
    last_days: str | None = None
    ctime: str | None = None
    house_origin: str | None = None
    house_id: str | None = None
    acn_house_id: str | None = None
    resblock_id: str | None = None
    res_block_info: str | None = None
    vr_status: int | None = None
    owner_reserve_price: str | None = None
    inventory_score: str | None = None
    del_status: int | None = None
    is_credential_completed: bool | None = None
    district_name: str | None = None
    biz_circle: str | None = None
    build_year: int | None = None
    build_type: str | None = None
    build_struct: str | None = None
    deal_prop: str | None = None
    house_usage: str | None = None
    tenement_fee: str | None = None
    heat_fee: str | None = None
    gas_fee: str | None = None
    water_type: str | None = None
    electric_type: str | None = None
    heat_type: str | None = None
    has_gas: str | None = None
    has_hot_water: str | None = None
    has_mid_water: str | None = None
    mid_water_fee: str | None = None
    hot_water_fee: str | None = None
    car_ratio: str | None = None
    car_onground: int | None = None
    car_underground: int | None = None
    park_fee: str | None = None
    has_lift: str | None = None
    lift_house_ratio: str | None = None
    school_info: str | None = None
    prop_years: str | None = None
    building_disgust: str | None = None
    external_url_lianjia: str | None = None
    external_url_beike: str | None = None
    vr_url: str | None = None
    net_work_status: int | None = None

    @classmethod
    def from_domain(cls, detail: SaleListingDetail) -> "SaleListingDetailResponse":
        return cls(**detail.__dict__)


class SaleMaintainFieldResponse(BaseModel):
    key: str
    name: str
    value: str | None = None
    important: bool = False
    comment: str | None = None


class SaleMaintainModuleResponse(BaseModel):
    name: str
    fields: list[SaleMaintainFieldResponse] = Field(default_factory=list)


class SaleMaintainInfoResponse(BaseModel):
    listing_id: str
    modules: list[SaleMaintainModuleResponse] = Field(default_factory=list)
    important_fields: list[SaleMaintainFieldResponse] = Field(default_factory=list)
    complete_rate: str | None = None
    last_update_time: datetime | None = None
    remark: str | None = None

    @classmethod
    def from_domain(cls, info: SaleMaintainInfo) -> "SaleMaintainInfoResponse":
        return cls(
            listing_id=info.listing_id,
            modules=[
                SaleMaintainModuleResponse(
                    name=module.name,
                    fields=[
                        SaleMaintainFieldResponse(**field.__dict__)
                        for field in module.fields
                    ],
                )
                for module in info.modules
            ],
            important_fields=[
                SaleMaintainFieldResponse(**field.__dict__)
                for field in info.important_fields
            ],
            complete_rate=info.complete_rate,
            last_update_time=info.last_update_time,
            remark=info.remark,
        )


class SaleFollowRecordResponse(BaseModel):
    follow_id: int | None = None
    content: str | None = None
    creator_name: str | None = None
    create_time: str | None = None
    on_top: bool = False
    remarks: str | None = None
    follow_label: str | None = None
    video_url: str | None = None

    @classmethod
    def from_domain(cls, record: SaleFollowRecord) -> "SaleFollowRecordResponse":
        return cls(**record.__dict__)


# ---------------------------------------------------------------------------
# 买卖 地图找房 (sale mapSearch) — house.link /search/sale/mapSearch.
# ---------------------------------------------------------------------------


class SaleMapSuggestionResponse(BaseModel):
    suggestion_id: str
    text: str
    alias: str | None = None
    bizcircle_name: str | None = None
    district_name: str | None = None
    item_type: str = ""
    count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    unit_price: float | None = None

    @classmethod
    def from_domain(cls, suggestion: SaleMapSuggestion) -> "SaleMapSuggestionResponse":
        return cls(**suggestion.__dict__)


class SaleMapNearbySearchRequest(BaseModel):
    """Semantic 买卖 map search: listings in communities within a radius."""

    location: str = Field(min_length=1, max_length=128)
    center_latitude: float | None = Field(default=None, ge=-90, le=90)
    center_longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: int = Field(default=1000, ge=100, le=5000)
    scope: SALE_SCOPE = "all"
    total_price_wan: NumericRange | None = Field(
        default=None,
        description="总价区间（万元），例如 50..70 对应 50-70万。",
    )
    area_sqm: NumericRange | None = Field(
        default=None,
        description="建筑面积区间（平米），例如 70..110。",
    )
    rooms: list[int] = Field(
        default_factory=list,
        max_length=8,
        description="房型室数（1..5，5 表示 5 室以上）。",
    )
    page: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_sale_map(self) -> "SaleMapNearbySearchRequest":
        if any(room < 1 or room > 5 for room in self.rooms):
            raise ValueError("rooms values must be in the range 1..5")
        if (self.center_latitude is None) != (self.center_longitude is None):
            raise ValueError("center_latitude and center_longitude must be supplied together")
        return self

    def to_domain(self) -> SaleMapNearbySearchFilters:
        return SaleMapNearbySearchFilters(
            location=self.location,
            center_latitude=self.center_latitude,
            center_longitude=self.center_longitude,
            radius_meters=self.radius_meters,
            scope=self.scope,
            price_wan=(
                (self.total_price_wan.min, self.total_price_wan.max)
                if self.total_price_wan
                else None
            ),
            area_sqm=(self.area_sqm.min, self.area_sqm.max) if self.area_sqm else None,
            rooms=tuple(sorted(set(self.rooms))),
            page=self.page,
        )


class SaleMapNearbySearchResponse(BaseModel):
    center: SaleMapSuggestionResponse
    radius_meters: int
    matched_community_count: int
    community_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Community ids whose centroids fall inside the radius circle. "
            "Pass as community_ids to sale_listing_search to continue filtering "
            "the same circle with the full 买卖 filter catalog. At most 100 ids "
            "are returned; check community_ids_truncated before treating the "
            "circle as complete."
        ),
    )
    community_ids_truncated: bool = Field(
        default=False,
        description="Whether the circle matched more communities than were returned.",
    )
    result: SaleListingPageResponse
    approximation: Literal["community_centroid"] = "community_centroid"

    @classmethod
    def from_domain(cls, result: SaleMapNearbySearchResult) -> "SaleMapNearbySearchResponse":
        return cls(
            center=SaleMapSuggestionResponse.from_domain(result.center),
            radius_meters=result.radius_meters,
            matched_community_count=result.matched_community_count,
            community_ids=list(result.community_ids),
            community_ids_truncated=result.community_ids_truncated,
            result=SaleListingPageResponse.from_domain(result.result),
        )
