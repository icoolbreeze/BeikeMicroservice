from __future__ import annotations

import logging
import threading
from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import router
from app.application.qr_login import CredentialInstaller, QrLoginManager, auto_start_login
from app.application.service import ConnectorService
from app.application.session_watchdog import SessionWatchdog
from app.application.featured_snapshot_push import FeaturedSnapshotPusher
from app.application.workbench_browser import WorkbenchBrowser
from app.bootstrap import UNCONFIGURED_PROFILE, build_providers
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the HTTP shell around the connector's domain/application layers."""
    resolved = settings or load_settings()
    if not logging.getLogger().handlers:
        # uvicorn only configures its own loggers; without a root handler
        # every app.* log line (keepalive heartbeats, QR re-login events,
        # degradation warnings) is silently dropped. Configure once here so
        # the service process is observable end to end.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        # The keepalive probe emits its own httpx request line per tick;
        # the session_watchdog heartbeat above is the observable, so keep
        # the HTTP client itself quiet unless something actually fails.
        logging.getLogger("httpx").setLevel(logging.WARNING)
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
    if (
        resolved.upstream_profile != UNCONFIGURED_PROFILE
        and resolved.session_watchdog_enabled
    ):
        # The uvicorn process owns no keepalive timer (that lives in
        # `crm-authd serve`), so credentials can go stale mid-run with nobody
        # probing or re-triggering login. The watchdog probes on a short
        # interval and re-opens the QR window whenever the session is
        # auth_required and no scan is pending.
        app.state.crm_session_watchdog = SessionWatchdog(
            session_provider=session_provider,
            qr_manager=app.state.crm_qr_login_manager,
            check_interval_seconds=resolved.session_watchdog_check_interval_seconds,
        )
        app.state.crm_session_watchdog.start()
    credential_store = getattr(session_provider, "_store", None)
    if credential_store is None and resolved.upstream_profile != UNCONFIGURED_PROFILE:
        credential_store = WindowsDpapiCredentialStore(resolved.credential_store_path)
    app.state.crm_credential_store = credential_store
    app.state.crm_workbench_opener = WorkbenchBrowser(
        resolved,
        credential_store,
        session_provider=session_provider
        if resolved.upstream_profile != UNCONFIGURED_PROFILE
        else None,
    )

    @app.on_event("shutdown")
    def stop_workbench_browser() -> None:
        app.state.crm_workbench_opener.close()

    if resolved.featured_push_enabled:
        app.state.crm_featured_snapshot_pusher = FeaturedSnapshotPusher(resolved)
        app.state.crm_featured_snapshot_pusher.start()

        @app.on_event("shutdown")
        def stop_featured_snapshot_pusher() -> None:
            app.state.crm_featured_snapshot_pusher.stop()
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    return app
