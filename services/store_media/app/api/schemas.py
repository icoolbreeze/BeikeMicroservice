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
