from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.application.service import ConnectorService
from app.infrastructure.kecom_crm_client import KecomCrmClient
from app.infrastructure.kecom_qr_bootstrap import KeComQrBootstrapProvider
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.unconfigured_crm_client import UnconfiguredCrmClient
from app.infrastructure.unconfigured_session_provider import UnconfiguredSessionProvider
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore

_PROFILE_UNCONFIGURED = "unconfigured"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the HTTP shell around the connector's domain/application layers."""
    resolved = settings or load_settings()
    session_provider, crm_client = _build_providers(resolved)

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
    app.state.crm_credential_store = (
        WindowsDpapiCredentialStore(resolved.credential_store_path)
        if resolved.upstream_profile != _PROFILE_UNCONFIGURED
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


def _build_providers(settings: Settings):
    """Select providers based on ``upstream_profile``.

    The default ``unconfigured`` profile keeps the original safe stubs so the
    FastAPI app can boot in CI without any real CRM upstream. Any other
    profile is treated as "this VM is wired to ke.com SSO": we construct a
    real ``KecomSessionProvider`` backed by the Windows DPAPI credential
    store and a ``KecomCrmClient`` that drives the upstream through that
    session boundary.
    """
    if settings.upstream_profile == _PROFILE_UNCONFIGURED:
        return UnconfiguredSessionProvider(), UnconfiguredCrmClient()

    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    bootstrap = KeComQrBootstrapProvider(settings)
    session = KecomSessionProvider(settings, store, bootstrap)
    crm_client = KecomCrmClient(session)
    return session, crm_client
