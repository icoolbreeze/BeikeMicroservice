from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import router
from app.application.service import StoreMediaService
from app.infrastructure.database import Database
from app.infrastructure.featured_fetcher import FeaturedListingsFetcher, FeaturedSnapshotStore
from app.infrastructure.news_fetcher import OfficialNewsFetcher
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.weather_fetcher import OpenMeteoWeatherFetcher


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    app_service = StoreMediaService(resolved, Database(resolved.database_path))
    news_fetcher = OfficialNewsFetcher(
        resolved.news_feed_url,
        limit=resolved.news_feed_size,
        cache_seconds=resolved.news_cache_seconds,
    )
    weather_fetcher = OpenMeteoWeatherFetcher(
        resolved.weather_latitude,
        resolved.weather_longitude,
        location_name=resolved.weather_location_name,
        cache_seconds=resolved.weather_cache_seconds,
    )
    featured_fetcher = FeaturedListingsFetcher(
        resolved.crm_connector_base_url,
        cache_seconds=resolved.featured_cache_seconds,
    )
    featured_snapshot_store = FeaturedSnapshotStore(resolved.featured_snapshot_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        app_service.initialize()
        yield

    app = FastAPI(
        title="store_media",
        description="门店多媒体广告发布、房源展示与角色管理服务",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.store_media_service = app_service
    app.state.news_fetcher = news_fetcher
    app.state.weather_fetcher = weather_fetcher
    app.state.featured_fetcher = featured_fetcher
    app.state.featured_snapshot_store = featured_snapshot_store
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
