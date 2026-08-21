from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.api.schemas import (
    CreateStoreRequest, CreateUserRequest, FeaturedFeedResponse,
    FeaturedListingSchema, LoginRequest, LoginResponse, MediaResponse,
    NewsFeedResponse, NewsItemResponse, PlaylistItem, PlaylistResponse,
    RoughcastProspectGalleryResponse, RoughcastProspectPhotoSchema,
    RoughcastRentalFeedResponse, RoughcastRentalListingSchema,
    StoreResponse, UpdateMediaRequest, UpdatePlaylistRequest, UpdateUserRequest,
    UserResponse, WeatherResponse,
)
from app.application.service import ServiceError, StoreMediaService
from app.domain.models import User
from app.domain.policies import ROLE_LABELS, ROLE_PERMISSIONS

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)


def service(request: Request) -> StoreMediaService:
    return request.app.state.store_media_service


def token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="请先登录")
    return credentials.credentials


def current_user(raw_token: Annotated[str, Depends(token)],
                 svc: Annotated[StoreMediaService, Depends(service)]) -> User:
    try:
        return svc.authenticate(raw_token)
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def invoke(call):
    try:
        return call()
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "store_media"}


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, svc: Annotated[StoreMediaService, Depends(service)]):
    raw_token, user, expires_at = invoke(lambda: svc.login(payload.username, payload.password))
    return LoginResponse(access_token=raw_token, expires_at=expires_at, user=UserResponse.model_validate(user))


@router.post("/auth/logout", status_code=204)
def logout(raw_token: Annotated[str, Depends(token)], svc: Annotated[StoreMediaService, Depends(service)]):
    svc.logout(raw_token)


@router.get("/auth/me", response_model=UserResponse)
def me(user: Annotated[User, Depends(current_user)]):
    return user


@router.get("/roles")
def roles(_user: Annotated[User, Depends(current_user)]):
    return [{"id": role.value, "label": ROLE_LABELS[role], "permissions": ROLE_PERMISSIONS[role]}
            for role in ROLE_LABELS]


@router.post("/stores", response_model=StoreResponse, status_code=201)
def create_store(payload: CreateStoreRequest, user: Annotated[User, Depends(current_user)],
                 svc: Annotated[StoreMediaService, Depends(service)]):
    return invoke(lambda: svc.create_store(user, store_id=payload.id, name=payload.name,
                                           region_id=payload.region_id))


@router.get("/stores", response_model=list[StoreResponse])
def list_stores(user: Annotated[User, Depends(current_user)],
                svc: Annotated[StoreMediaService, Depends(service)]):
    return svc.list_stores(user)


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: CreateUserRequest, user: Annotated[User, Depends(current_user)],
                svc: Annotated[StoreMediaService, Depends(service)]):
    return invoke(lambda: svc.create_user(user, **payload.model_dump()))


@router.get("/users", response_model=list[UserResponse])
def list_users(user: Annotated[User, Depends(current_user)],
               svc: Annotated[StoreMediaService, Depends(service)]):
    return svc.list_users(user)


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: str, payload: UpdateUserRequest,
                user: Annotated[User, Depends(current_user)],
                svc: Annotated[StoreMediaService, Depends(service)]):
    return invoke(lambda: svc.update_user(user, user_id, **payload.model_dump()))


@router.post("/media", response_model=MediaResponse, status_code=201)
async def upload_media(
    user: Annotated[User, Depends(current_user)],
    svc: Annotated[StoreMediaService, Depends(service)],
    store_id: Annotated[str, Form()],
    title: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    image_duration_seconds: Annotated[float | None, Form()] = None,
    is_published: Annotated[bool, Form()] = True,
):
    try:
        return await run_in_threadpool(
            svc.upload_media, user, store_id=store_id, title=title,
            original_name=file.filename or "media", source=file.file,
            image_duration_seconds=image_duration_seconds, is_published=is_published,
        )
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/media", response_model=list[MediaResponse])
def list_media(store_id: str, user: Annotated[User, Depends(current_user)],
               svc: Annotated[StoreMediaService, Depends(service)]):
    return invoke(lambda: svc.list_media(user, store_id))


@router.put("/media/playlist", response_model=list[MediaResponse])
def update_playlist(store_id: str, payload: UpdatePlaylistRequest,
                    user: Annotated[User, Depends(current_user)],
                    svc: Annotated[StoreMediaService, Depends(service)]):
    updates = [item.model_dump() for item in payload.items]
    return invoke(lambda: svc.update_playlist(
        user, store_id, updates=updates, delete_ids=payload.delete_ids
    ))


@router.put("/media/{media_id}", response_model=MediaResponse)
def update_media(media_id: str, payload: UpdateMediaRequest,
                 user: Annotated[User, Depends(current_user)],
                 svc: Annotated[StoreMediaService, Depends(service)]):
    return invoke(lambda: svc.update_media(user, media_id, **payload.model_dump()))


@router.delete("/media/{media_id}", status_code=204)
def delete_media(media_id: str, user: Annotated[User, Depends(current_user)],
                 svc: Annotated[StoreMediaService, Depends(service)]):
    invoke(lambda: svc.delete_media(user, media_id))


@router.get("/display/{store_id}/playlist", response_model=PlaylistResponse)
def playlist(store_id: str, svc: Annotated[StoreMediaService, Depends(service)]):
    store, items = invoke(lambda: svc.public_playlist(store_id))
    return PlaylistResponse(
        store=StoreResponse.model_validate(store),
        items=[PlaylistItem(
            id=item.id, title=item.title, media_type=item.media_type,
            image_duration_seconds=item.image_duration_seconds,
            content_url=f"/api/v1/display/media/{item.id}/content",
        ) for item in items],
    )


@router.get("/display/media/{media_id}/content", name="display-media-content")
def media_content(media_id: str, svc: Annotated[StoreMediaService, Depends(service)]):
    item, path = invoke(lambda: svc.public_media_path(media_id))
    return FileResponse(path, media_type=item.content_type, filename=item.original_name,
                        content_disposition_type="inline",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/display/news", response_model=NewsFeedResponse)
def display_news(request: Request) -> NewsFeedResponse:
    fetcher = request.app.state.news_fetcher
    items = fetcher.latest()
    return NewsFeedResponse(
        source=fetcher.source_label,
        items=[NewsItemResponse(title=item.title, url=item.url, published_at=item.published_at)
               for item in items],
    )


@router.get("/display/weather", response_model=WeatherResponse)
def display_weather(request: Request) -> WeatherResponse:
    fetcher = request.app.state.weather_fetcher
    reading = fetcher.latest()
    if reading is None:
        raise HTTPException(status_code=503, detail="天气数据暂不可用")
    return WeatherResponse(
        location=fetcher.location_name,
        temperature_c=reading.temperature_c,
        description=reading.description,
        icon=reading.icon,
        observed_at=reading.observed_at,
    )


@router.get("/display/featured", response_model=FeaturedFeedResponse)
def display_featured(request: Request) -> FeaturedFeedResponse:
    snapshot = request.app.state.featured_snapshot_store.latest()
    feed = snapshot if snapshot is not None else request.app.state.featured_fetcher.latest()
    return FeaturedFeedResponse(
        sale=[FeaturedListingSchema.model_validate(item) for item in feed.sale],
        rent=[FeaturedListingSchema.model_validate(item) for item in feed.rent],
        sale_total=feed.sale_total,
        rent_total=feed.rent_total,
        updated_at=feed.updated_at,
    )


@router.get("/display/roughcast-rentals", response_model=RoughcastRentalFeedResponse)
def display_roughcast_rentals(
    request: Request,
    page: int = Query(default=1, ge=1, le=1000),
) -> RoughcastRentalFeedResponse:
    feed = request.app.state.roughcast_rental_fetcher.latest(page=page)
    if feed is None:
        raise HTTPException(status_code=503, detail="房源数据暂不可用")
    return RoughcastRentalFeedResponse(
        items=[RoughcastRentalListingSchema.model_validate(item) for item in feed.items],
        updated_at=feed.updated_at,
        page=feed.page,
        has_more=feed.has_more,
    )


@router.get(
    "/display/roughcast-rentals/{listing_id}/prospect",
    response_model=RoughcastProspectGalleryResponse,
)
def display_roughcast_prospect(
    listing_id: str,
    request: Request,
) -> RoughcastProspectGalleryResponse:
    fetcher = request.app.state.roughcast_rental_fetcher
    if not fetcher.knows_listing(listing_id):
        raise HTTPException(status_code=404, detail="房源不在当前浏览结果中")
    gallery = fetcher.prospect(listing_id)
    if gallery is None:
        raise HTTPException(status_code=503, detail="实勘图片暂不可用")
    return RoughcastProspectGalleryResponse(
        photos=[RoughcastProspectPhotoSchema.model_validate(photo) for photo in gallery.photos],
    )
