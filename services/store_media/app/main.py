from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response


from app.api.router import login as store_media_login
from app.api.router import router
from app.api.schemas import LoginRequest
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
    # CRM connector 反代必须在 include_router 之前注册：门店后台自身也有
    # POST /api/v1/auth/login（JSON 账号密码），只有先注册的路由才能截获
    # 无 body 的扫码登录 POST。
    crm_base = resolved.crm_connector_base_url.rstrip("/")

    def _proxy_crm(
        path: str,
        *,
        timeout: float = 8.0,
        method: str = "GET",
        data: bytes | None = None,
    ) -> Response:
        try:
            upstream_request = UrlRequest(
                f"{crm_base}{path}", data=data, method=method
            )
            with urlopen(upstream_request, timeout=timeout) as upstream:
                body = upstream.read()
                media = upstream.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                return Response(
                    content=body,
                    status_code=upstream.status,
                    media_type=media,
                )
        except HTTPError as exc:
            # 上游明确返回错误状态时（无 pending 登录为 404、冲突为 409 …）
            # 原样透传，不要吞成 502。
            body = exc.read()
            media = (
                exc.headers.get("Content-Type", "application/octet-stream")
                if exc.headers is not None
                else "application/octet-stream"
            )
            return Response(content=body, status_code=exc.code, media_type=media)
        except TimeoutError:
            return Response(
                content="crm connector timeout",
                status_code=504,
                media_type="text/plain; charset=utf-8",
            )
        except URLError as exc:
            return Response(
                content=f"crm connector unavailable: {exc}",
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )

    @app.get("/login", include_in_schema=False)
    def crm_login_page() -> Response:
        return _proxy_crm("/login", timeout=20)

    @app.get("/api/v1/connection/status", include_in_schema=False)
    def crm_connection_status() -> Response:
        return _proxy_crm("/api/v1/connection/status", timeout=20)

    @app.post("/api/v1/auth/login", include_in_schema=False)
    async def crm_qr_login(request: Request) -> Response:
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        if body or content_type:
            # 门店后台自身的账号密码登录（JSON）：交给原有登录逻辑处理，
            # 保持后台登录不受扫码登录代理影响。
            payload = LoginRequest.model_validate_json(body or b"{}")
            return store_media_login(
                payload, request.app.state.store_media_service
            )
        # 无 body：crm-connector 的扫码登录。首张二维码可能要数秒。
        return await run_in_threadpool(
            _proxy_crm,
            "/api/v1/auth/login",
            timeout=20,
            method="POST",
            data=b"",
        )

    @app.get("/api/v1/auth/login/current", include_in_schema=False)
    def crm_current_qr_login() -> Response:
        return _proxy_crm("/api/v1/auth/login/current", timeout=20)

    @app.get("/api/v1/auth/login/{login_id}", include_in_schema=False)
    def crm_qr_login_status(login_id: str) -> Response:
        return _proxy_crm(f"/api/v1/auth/login/{login_id}", timeout=20)

    @app.get("/api/v1/auth/login/{login_id}/qrcode.png", include_in_schema=False)
    def crm_qr_login_qrcode(login_id: str) -> Response:
        return _proxy_crm(
            f"/api/v1/auth/login/{login_id}/qrcode.png", timeout=20
        )

    @app.get("/archive", include_in_schema=False)
    def rental_archive_dashboard() -> Response:
        return _proxy_crm("/archive")

    @app.get("/api/v1/archive/rental/status", include_in_schema=False)
    def rental_archive_status() -> Response:
        return _proxy_crm("/api/v1/archive/rental/status", timeout=30)

    @app.get("/api/v1/archive/sale/status", include_in_schema=False)
    def sale_archive_status() -> Response:
        return _proxy_crm("/api/v1/archive/sale/status", timeout=30)

    app.include_router(router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", RevalidatedStaticFiles(directory=static_dir, html=True), name="static")
    return app
