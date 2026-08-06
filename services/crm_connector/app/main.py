from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.application.service import ConnectorService
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.unconfigured_crm_client import UnconfiguredCrmClient
from app.infrastructure.unconfigured_session_provider import UnconfiguredSessionProvider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the HTTP shell around the connector's domain/application layers."""
    resolved = settings or load_settings()
    session_provider = UnconfiguredSessionProvider()
    crm_client = UnconfiguredCrmClient()

    app = FastAPI(
        title="crm_connector",
        description="Personal CRM MCP Connector service scaffold",
        version="0.1.0",
    )
    app.state.crm_connector_service = ConnectorService(
        settings=resolved,
        session_provider=session_provider,
        crm_client=crm_client,
    )
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    return app
