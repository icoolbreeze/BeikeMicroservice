"""Provider assembly for the connector service and the MCP stdio entry point."""

from __future__ import annotations

from app.application.service import ConnectorService
from app.domain.providers.crm_client import CrmClient
from app.domain.providers.session_provider import SessionProvider
from app.infrastructure.kecom_crm_client import KecomCrmClient
from app.infrastructure.kecom_qr_bootstrap import KeComQrBootstrapProvider
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings
from app.infrastructure.unconfigured_crm_client import UnconfiguredCrmClient
from app.infrastructure.unconfigured_session_provider import UnconfiguredSessionProvider
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore

UNCONFIGURED_PROFILE = "unconfigured"


def build_providers(settings: Settings) -> tuple[SessionProvider, CrmClient]:
    """Select session/CRM providers based on ``upstream_profile``.

    The default profile is the real ke.com SSO wiring (``kecom-prod``), so a
    plain service start pops the native QR login window whenever no active
    credential exists. Set ``CC_UPSTREAM_PROFILE=unconfigured`` to keep the
    safe stubs instead, which lets the FastAPI app and MCP server boot in CI
    without any real CRM upstream.
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
