from __future__ import annotations

import threading
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.application.qr_login import CredentialInstaller, QrLoginManager, auto_start_login
from app.application.service import ConnectorService
from app.bootstrap import UNCONFIGURED_PROFILE, build_providers
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the HTTP shell around the connector's domain/application layers."""
    resolved = settings or load_settings()
    session_provider, crm_client = build_providers(resolved)

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
    app.state.crm_session_provider = session_provider
    app.state.crm_qr_login_manager = (
        QrLoginManager(resolved, cast(CredentialInstaller, session_provider))
        if resolved.upstream_profile != UNCONFIGURED_PROFILE
        else None
    )
    if app.state.crm_qr_login_manager is not None and resolved.qr_login_auto_start:
        # When the service boots unauthenticated, kick off a QR login in the
        # background so the native popup appears without any browser or API
        # interaction. Conflicts (already ready) are ignored inside the helper.
        threading.Thread(
            target=auto_start_login,
            args=(app.state.crm_qr_login_manager,),
            name="crm-qr-login-auto-start",
            daemon=True,
        ).start()
    app.state.crm_credential_store = (
        WindowsDpapiCredentialStore(resolved.credential_store_path)
        if resolved.upstream_profile != UNCONFIGURED_PROFILE
        else None
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
