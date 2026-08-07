"""Provider assembly for the connector service and the MCP stdio entry point."""

from __future__ import annotations

from app.application.service import ConnectorService
from app.infrastructure.kecom_crm_client import KecomCrmClient
from app.infrastructure.kecom_qr_bootstrap import KeComQrBootstrapProvider
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings
from app.infrastructure.unconfigured_crm_client import UnconfiguredCrmClient
from app.infrastructure.unconfigured_session_provider import UnconfiguredSessionProvider
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore

UNCONFIGURED_PROFILE = "unconfigured"


def build_providers(settings: Settings):
    """Select session/CRM providers based on ``upstream_profile``.

    The default ``unconfigured`` profile keeps the safe stubs so the FastAPI
    app and MCP server can boot in CI without any real CRM upstream. Any
    other profile is treated as "this VM is wired to ke.com SSO": a real
    ``KecomSessionProvider`` backed by the Windows DPAPI credential store
    and a ``KecomCrmClient`` that drives the upstream through that session
    boundary.
    """
    if settings.upstream_profile == UNCONFIGURED_PROFILE:
        return UnconfiguredSessionProvider(), UnconfiguredCrmClient()

    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    bootstrap = KeComQrBootstrapProvider(settings)
    session = KecomSessionProvider(settings, store, bootstrap)
    crm_client = KecomCrmClient(session)
    return session, crm_client


def build_service(settings: Settings) -> ConnectorService:
    """Assemble the domain service with the providers for the active profile."""
    session_provider, crm_client = build_providers(settings)
    return ConnectorService(
        settings=settings,
        session_provider=session_provider,
        crm_client=crm_client,
    )
