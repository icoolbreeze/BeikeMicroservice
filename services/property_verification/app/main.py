"""服务入口：组装 FastAPI 应用、挂载路由、注册生命周期钩子。

启动：在服务目录下执行
    uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.dependencies import get_settings, get_verification_service
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭钩子：初始化存储目录与线程池，关闭时释放。"""
    settings = get_settings()
    # 确保存储目录存在
    settings.jobs_root  # 触发创建
    svc = get_verification_service()
    app.state.verification_service = svc
    try:
        yield
    finally:
        svc.shutdown()


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()
    app = FastAPI(
        title="property_verification",
        description="房源信息验证微服务：上传证件 -> 提取字段 -> 蓉e办验证 -> 生成截图",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True),
                 name="static")

    return app
