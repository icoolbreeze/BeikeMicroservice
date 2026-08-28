from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


from app.api.router import router
from app.application.roughcast_loop import build_daily_loop
from app.application.service import StoreMediaService
from app.infrastructure.baidu_map_client import BaiduMapClient
from app.infrastructure.database import Database
from app.infrastructure.featured_fetcher import FeaturedListingsFetcher, FeaturedSnapshotStore
from app.infrastructure.news_fetcher import OfficialNewsFetcher
from app.infrastructure.roughcast_rental_fetcher import RoughcastRentalFetcher
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.weather_fetcher import OpenMeteoWeatherFetcher
from app.infrastructure.district_catalog import DistrictCatalogFetcher
from app.infrastructure.workbench_open_client import WorkbenchOpenClient


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
        # 清水房本地日 loop（`roughcast_loop.py`）。默认关闭,开关打开
        # 时只起这一个线程——`roughcast-daily-loop`——它内部已经包了
        # 队列 A / Shadow score / 队列 B 的编排,**不要**再额外起 A 的
        # 守护线程,否则同一日凌晨会跑出两轮 A run,把硬顶撞穿。
        loop = build_daily_loop(resolved, database) if resolved.roughcast_crawl_enabled else None
        _app.state.roughcast_loop = loop
        # 兼容老代码里读 `app.state.roughcast_crawler` 的路径(目前只有
        # 测试 + 一些 debug 探针):把 A 实例暴露出去,但不要起它的
        # 守护线程。A 的 daemon 入口由 loop 统一调度。
        _app.state.roughcast_crawler = loop.queue_a if loop is not None else None
        if loop is not None:
            loop.start()
        try:
            yield
        finally:
            if loop is not None:
                loop.stop()

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
    app.state.workbench_open_client = WorkbenchOpenClient(resolved.crm_connector_base_url)
    app.state.district_catalog_fetcher = DistrictCatalogFetcher(resolved.crm_connector_base_url)
    app.state.baidu_map_client = (
        BaiduMapClient(resolved.baidu_map_ak) if resolved.baidu_map_ak else None
    )
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
