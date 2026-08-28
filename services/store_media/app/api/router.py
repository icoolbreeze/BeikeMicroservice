from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from app.api.schemas import (
    CreateStoreRequest, CreateUserRequest, FeaturedFeedResponse,
    FeaturedListingSchema, LoginRequest, LoginResponse, MediaResponse,
    NewsFeedResponse, NewsItemResponse, PlaylistItem, PlaylistResponse,
    RoughcastProspectGalleryResponse, RoughcastProspectPhotoSchema,
    RoughcastRentalFeedResponse, RoughcastRentalListingSchema,
    RoughcastDistrictCountSchema, RoughcastScoreReviewCardSchema,
    RoughcastScoreReviewResponse, RoughcastWorkbenchOpenResponse,
    RoughcastRankedCardSchema, RoughcastRankedDistrictCountSchema,
    RoughcastRankedResponse,
    StoreResponse, UpdateMediaRequest, UpdatePlaylistRequest, UpdateUserRequest,
    UserResponse, WeatherResponse,
)
from app.application.roughcast_review import build_review_feed
from app.application.roughcast_ranked import build_ranked_feed
from app.application.service import ServiceError, StoreMediaService
from app.infrastructure.workbench_open_client import WorkbenchOpenError
from app.domain.models import User
from app.domain.policies import ROLE_LABELS, ROLE_PERMISSIONS

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


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


@router.get("/display/roughcast-score-review", response_model=RoughcastScoreReviewResponse)
def display_roughcast_score_review(
    request: Request,
    district: list[str] | None = Query(default=None, max_length=32),
    require_cover: bool = Query(default=False),
) -> RoughcastScoreReviewResponse:
    """内部核分清单。读最新 COMPLETE 评分批次,不上公开排名页。"""
    from app.infrastructure.roughcast_repository import RoughcastRepository

    repository = RoughcastRepository(request.app.state.store_media_service.database)
    catalog = repository.load_district_catalog()
    if not catalog.bizcircle_to_districts:
        catalog = request.app.state.district_catalog_fetcher.latest()
    feed = build_review_feed(
        repository,
        catalog=catalog,
        districts_filter=district,
        require_cover=require_cover,
    )
    if feed is None:
        raise HTTPException(status_code=404, detail="还没有完成的评分批次")
    return RoughcastScoreReviewResponse(
        score_run_id=feed.score_run_id,
        delta_value=feed.delta_value,
        scored_count=feed.scored_count,
        filtered_count=feed.filtered_count,
        selected_district=feed.selected_district,
        selected_districts=list(feed.selected_districts),
        require_cover=feed.require_cover,
        districts=[
            RoughcastDistrictCountSchema(name=item.name, count=item.count)
            for item in feed.districts
        ],
        groups={
            name: [RoughcastScoreReviewCardSchema.model_validate(card.__dict__) for card in cards]
            for name, cards in feed.groups.items()
        },
    )


@router.get("/display/roughcast-ranked", response_model=RoughcastRankedResponse)
def display_roughcast_ranked(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=50),
    sort: str = Query(default="quality"),
    min_confidence: int = Query(default=0, ge=0, le=100),
    district: list[str] | None = Query(default=None, max_length=32),
    deals: bool = Query(default=False),
    group: str = Query(default="scored"),
    require_cover: bool = Query(default=False),
) -> RoughcastRankedResponse:
    """本地自用排名榜:读最新 COMPLETE 评分批次,零上游。

    选房场景:同一榜单上按 `sort` 切维度(quality / confidence / 月租 /
    单价 / 最新)。`deals=true` 是「高可信捡漏」,强制走 scored 集合并把
    周边估算排除在外。`group` 可切到 nearby / insufficient / data_error,
    三组互不相交且不带 quality_score 整数。
    """
    from app.infrastructure.roughcast_repository import RoughcastRepository

    repository = RoughcastRepository(request.app.state.store_media_service.database)
    catalog = repository.load_district_catalog()
    if not catalog.bizcircle_to_districts:
        catalog = request.app.state.district_catalog_fetcher.latest()
    feed = build_ranked_feed(
        repository,
        catalog=catalog,
        page=page,
        page_size=page_size,
        sort=sort,
        min_confidence=min_confidence,
        districts_filter=district,
        deals=deals,
        group=group,
        require_cover=require_cover,
    )
    if feed is None:
        raise HTTPException(status_code=404, detail="还没有完成的评分批次")
    return RoughcastRankedResponse(
        score_run_id=feed.score_run_id,
        model_version=feed.model_version,
        delta_version=feed.delta_version,
        delta_value=feed.delta_value,
        scored_at=feed.scored_at,
        listing_run_id=feed.listing_run_id,
        sort_applied=feed.sort_applied,
        group=feed.group,
        deals=feed.deals,
        min_confidence=feed.min_confidence,
        require_cover=feed.require_cover,
        selected_district=feed.selected_district,
        selected_districts=list(feed.selected_districts),
        districts=[
            RoughcastRankedDistrictCountSchema(name=item.name, count=item.count)
            for item in feed.districts
        ],
        group_counts=dict(feed.group_counts),
        total=feed.total,
        page=feed.page,
        page_size=feed.page_size,
        has_more=feed.has_more,
        items=[RoughcastRankedCardSchema.model_validate(card.__dict__) for card in feed.items],
    )


@router.post(
    "/display/roughcast-score-review/{listing_id}/open",
    response_model=RoughcastWorkbenchOpenResponse,
)
def open_roughcast_score_review_listing(
    listing_id: str,
    request: Request,
) -> RoughcastWorkbenchOpenResponse:
    """核分页打开房源：由 crm_connector 注入 CRM Cookie 后打开工作台详情。"""
    try:
        payload = request.app.state.workbench_open_client.open_rental_listing(listing_id)
    except WorkbenchOpenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(url, str) or not url:
        raise HTTPException(status_code=503, detail="工作台已触发，但没有返回页面地址")
    from app.infrastructure.roughcast_repository import RoughcastRepository
    view_count = RoughcastRepository(
        request.app.state.store_media_service.database
    ).increment_review_view(str(payload.get("listing_id") or listing_id))
    return RoughcastWorkbenchOpenResponse(
        listing_id=str(payload.get("listing_id") or listing_id),
        url=url,
        opened=bool(payload.get("opened", True)),
        view_count=view_count,
    )


@router.get("/display/roughcast-ranked/{listing_id}/share")
def share_roughcast_ranked_listing(
    listing_id: str,
    request: Request,
):
    """分享海报 PNG：标题/事实/全市地图/实勘图/页脚,纵向长图 1080 宽。"""
    from io import BytesIO

    from PIL import Image

    from app.application.roughcast_share import (
        ShareProspectPhoto,
        build_facts_from_row,
        compose_share_poster,
        preload_prospect_photos,
        is_valid_listing_id,
    )
    from app.infrastructure.baidu_map_client import (
        BaiduMapError,
        fetch_static_city_map,
    )
    from app.infrastructure.roughcast_repository import RoughcastRepository

    if not is_valid_listing_id(listing_id):
        raise HTTPException(status_code=400, detail="房源编号无效")

    database = request.app.state.store_media_service.database
    repository = RoughcastRepository(database)
    run = repository.latest_score_run()
    if run is None:
        raise HTTPException(status_code=404, detail="还没有完成的评分批次")

    rows = repository.list_score_review_rows(int(run["id"]))
    row = next((r for r in rows if str(r["listing_id"]) == listing_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="房源不在最新评分批次中")
    row_dict = dict(row)

    city_lat: float | None = None
    city_lng: float | None = None
    try:
        if row_dict.get("community_latitude") is not None:
            city_lat = float(row_dict["community_latitude"])
        if row_dict.get("community_longitude") is not None:
            city_lng = float(row_dict["community_longitude"])
    except (TypeError, ValueError):
        city_lat = city_lng = None
    community_id = row_dict.get("community_id")
    if (city_lat is None or city_lng is None) and community_id:
        community = repository.community(str(community_id))
        if community is not None:
            try:
                if community["latitude"] is not None and community["longitude"] is not None:
                    city_lat = float(community["latitude"])
                    city_lng = float(community["longitude"])
            except (KeyError, TypeError, ValueError):
                city_lat = city_lng = None

    baidu_client = getattr(request.app.state, "baidu_map_client", None)
    if (city_lat is None or city_lng is None) and baidu_client is not None and row["community_name"]:
        try:
            hit = baidu_client.locate_community(str(row["community_name"]))
        except BaiduMapError:
            hit = None
        if hit is not None:
            city_lat = float(hit.latitude)
            city_lng = float(hit.longitude)

    facts = build_facts_from_row(row_dict, city_lat=city_lat, city_lng=city_lng)

    map_placeholder: str | None = None
    map_image = None
    if city_lat is None or city_lng is None:
        map_placeholder = "暂无坐标"
    elif baidu_client is None:
        map_placeholder = "未配置百度 AK"
    else:
        ak_value = getattr(baidu_client, "_ak", "")
        png_bytes: bytes | None = None
        try:
            png_bytes = fetch_static_city_map(
                ak_value, city_lng, city_lat,
                width=512, height=360, zoom=10,
            )
        except BaiduMapError as exc:
            logger.warning("share: static map fetch failed: %s", exc)
        if png_bytes:
            try:
                map_image = Image.open(BytesIO(png_bytes))
            except Exception as exc:                                # noqa: BLE001
                logger.warning("share: map decode failed: %s", exc)
                map_image = None
        if map_image is None:
            map_placeholder = "地图抓取失败"

    photos: list[ShareProspectPhoto] = []
    fetcher = request.app.state.roughcast_rental_fetcher
    gallery = fetcher.prospect_for_id(listing_id)
    if gallery is not None:
        photos = [
            ShareProspectPhoto(url=p.url, label=p.label or "实勘图片")
            for p in gallery.photos
        ]

    preloaded = preload_prospect_photos(photos) if photos else {}

    def _photo_loader(url: str):
        return preloaded.get(url)

    png = compose_share_poster(
        facts, map_image=map_image, map_placeholder=map_placeholder,
        photos=photos, photo_loader=_photo_loader,
    )
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="roughcast-{listing_id}.png"',
    }
    return Response(content=png, media_type="image/png", headers=headers)
