from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import router
from app.application.service import StoreMediaService
from app.infrastructure.database import Database
from app.infrastructure.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()
    app_service = StoreMediaService(resolved, Database(resolved.database_path))

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
