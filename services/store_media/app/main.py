from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


from app.api.router import router
from app.application.roughcast_crawler import build_crawler
from app.application.service import StoreMediaService
from app.infrastructure.database import Database
from app.infrastructure.featured_fetcher import FeaturedListingsFetcher, FeaturedSnapshotStore
from app.infrastructure.news_fetcher import OfficialNewsFetcher
from app.infrastructure.roughcast_rental_fetcher import RoughcastRentalFetcher
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.weather_fetcher import OpenMeteoWeatherFetcher


class RevalidatedStaticFiles(StaticFiles):
    """静态资源始终回源校验，避免浏览器启发式缓存滞留旧版前端。

    ETag / Last-Modified 仍然生效，未改动的文件返回 304，不重复传输。
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    database = Database(resolved.database_path)
    app_service = StoreMediaService(resolved, database)
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
    roughcast_rental_fetcher = RoughcastRentalFetcher(
        resolved.crm_connector_base_url,
        cache_seconds=resolved.roughcast_cache_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        app_service.initialize()
        # 清水房采集默认关闭:头几天用 scripts/roughcast_crawl_once.py 手动跑,
        # 确认上游流量曲线后再打开常驻线程（SM_ROUGHCAST_CRAWL_ENABLED=1）。
        crawler = build_crawler(resolved, database) if resolved.roughcast_crawl_enabled else None
        _app.state.roughcast_crawler = crawler
        if crawler is not None:
            crawler.start()
        try:
            yield
        finally:
            if crawler is not None:
                crawler.stop()

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
    app.state.roughcast_rental_fetcher = roughcast_rental_fetcher
    app.state.roughcast_crawler = None      # lifespan 里按开关决定是否装上
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", RevalidatedStaticFiles(directory=static_dir, html=True), name="static")
    return app
