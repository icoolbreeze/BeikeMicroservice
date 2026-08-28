from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import MediaType, Role


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    role: Role
    region_id: str | None
    store_id: str | None
    is_active: bool
    created_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class CreateStoreRequest(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=100)
    region_id: str


class StoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    region_id: str
    is_active: bool
    created_at: datetime


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=100)
    role: Role
    region_id: str | None = None
    store_id: str | None = None


class UpdateUserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    role: Role
    region_id: str | None = None
    store_id: str | None = None
    is_active: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=256)


class UpdateMediaRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    image_duration_seconds: float | None = None
    sort_order: int = Field(default=0, ge=0, le=100_000)
    is_published: bool = False


class PlaylistItemUpdate(BaseModel):
    id: str
    title: str = Field(min_length=1, max_length=200)
    image_duration_seconds: float | None = None
    sort_order: int = Field(default=0, ge=0, le=100_000)
    is_published: bool = False


class UpdatePlaylistRequest(BaseModel):
    items: list[PlaylistItemUpdate] = Field(max_length=1000)
    delete_ids: list[str] = Field(default_factory=list, max_length=1000)


class MediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    store_id: str
    title: str
    media_type: MediaType
    content_type: str
    original_name: str
    image_duration_seconds: float | None
    sort_order: int
    is_published: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class PlaylistItem(BaseModel):
    id: str
    title: str
    media_type: MediaType
    image_duration_seconds: float | None
    content_url: str


class PlaylistResponse(BaseModel):
    store: StoreResponse
    items: list[PlaylistItem]


class NewsItemResponse(BaseModel):
    title: str
    url: str
    published_at: str = ""


class NewsFeedResponse(BaseModel):
    source: str
    items: list[NewsItemResponse]


class WeatherResponse(BaseModel):
    location: str
    temperature_c: float
    description: str
    icon: str
    observed_at: str = ""


class FeaturedTagSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    type: str
    icon: str
    text: str


class FeaturedListingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    layout: str
    area: str
    floor: str
    orient: str
    decor: str
    price: str
    priceUnit: str
    unitPrice: str
    location: str
    tags: list[FeaturedTagSchema]
    image: str
    desc: str
    original_image: str | None = None


class FeaturedFeedResponse(BaseModel):
    sale: list[FeaturedListingSchema]
    rent: list[FeaturedListingSchema]
    sale_total: int | None = None
    rent_total: int | None = None
    updated_at: str = ""


class RoughcastRentalListingSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listing_id: str | None = None
    community: str
    layout: str
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    orientation: str
    floor: str
    image: str | None = None


class RoughcastRentalFeedResponse(BaseModel):
    items: list[RoughcastRentalListingSchema]
    updated_at: str = ""
    page: int
    has_more: bool


class RoughcastProspectPhotoSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url: str
    label: str


class RoughcastProspectGalleryResponse(BaseModel):
    photos: list[RoughcastProspectPhotoSchema]


class RoughcastScoreReviewCardSchema(BaseModel):
    listing_id: str
    city_rank: int | None = None
    community: str
    district: str | None = None
    bizcircle: str | None = None
    layout: str
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    unit_rent: float | None = None
    reference_unit_rent: float | None = None
    expected_unit_rent: float | None = None
    quality_score: int | None = None
    confidence_score: int | None = None
    benchmark_mode: str | None = None
    extreme_price: bool = False
    reason: str = ""
    image: str | None = None
    ke_url: str
    group_hint: str = ""
    view_count: int = 0


class RoughcastDistrictCountSchema(BaseModel):
    name: str
    count: int


class RoughcastScoreReviewResponse(BaseModel):
    score_run_id: int
    delta_value: float | None = None
    scored_count: int
    filtered_count: int
    selected_district: str | None = None
    selected_districts: list[str] = Field(default_factory=list)
    require_cover: bool = False
    districts: list[RoughcastDistrictCountSchema] = Field(default_factory=list)
    groups: dict[str, list[RoughcastScoreReviewCardSchema]]


class RoughcastWorkbenchOpenResponse(BaseModel):
    listing_id: str
    url: str
    opened: bool = True
    view_count: int = 0


class RoughcastRankedCardSchema(BaseModel):
    listing_id: str
    city_rank: int | None = None
    community: str
    district: str | None = None
    bizcircle: str | None = None
    layout: str
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    orientation: str | None = None
    floor: str | None = None
    unit_rent: float | None = None
    reference_unit_rent: float | None = None
    expected_unit_rent: float | None = None
    advantage: float | None = None
    quality_score: int | None = None
    quality_score_raw: float | None = None
    quality_status: str
    quality_tier: str | None = None
    confidence_score: int | None = None
    benchmark_mode: str | None = None
    peer_scope: str | None = None
    extreme_price: bool = False
    reason: str = ""
    image: str | None = None
    ke_url: str
    view_count: int = 0


class RoughcastRankedDistrictCountSchema(BaseModel):
    name: str
    count: int


class RoughcastRankedResponse(BaseModel):
    score_run_id: int
    model_version: str | None = None
    delta_version: int | None = None
    delta_value: float | None = None
    scored_at: str | None = None
    listing_run_id: int | None = None
    sort_applied: str
    group: str
    deals: bool = False
    min_confidence: int = 0
    require_cover: bool = False
    selected_district: str | None = None
    selected_districts: list[str] = Field(default_factory=list)
    districts: list[RoughcastRankedDistrictCountSchema] = Field(default_factory=list)
    group_counts: dict[str, int] = Field(default_factory=dict)
    total: int
    page: int
    page_size: int
    has_more: bool
    items: list[RoughcastRankedCardSchema]
