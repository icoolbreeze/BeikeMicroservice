from __future__ import annotations

import logging
import threading
from typing import cast

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.router import router
from app.application.qr_login import CredentialInstaller, QrLoginManager, auto_start_login
from app.application.rental_archive import RentalArchiveRunner
from app.application.sale_archive import SaleArchiveRunner
from app.application.service import ConnectorService
from app.application.session_watchdog import SessionWatchdog
from app.application.featured_snapshot_push import FeaturedSnapshotPusher
from app.application.workbench_browser import WorkbenchBrowser
from app.bootstrap import UNCONFIGURED_PROFILE, build_providers
from app.infrastructure.rental_archive_store import RentalArchiveStore
from app.infrastructure.sale_archive_store import SaleArchiveStore
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

    # Always wired so /archive/rental/status answers while disabled; the
    # daemon thread only starts when the archive is explicitly enabled. The
    # store connects lazily, so wiring alone touches no filesystem.
    app.state.crm_rental_archive_runner = RentalArchiveRunner(
        resolved,
        app.state.crm_connector_service,
        RentalArchiveStore(resolved.rental_archive_db_path),
    )
    if resolved.rental_archive_enabled:
        app.state.crm_rental_archive_runner.start()

        @app.on_event("shutdown")
        def stop_rental_archive_runner() -> None:
            app.state.crm_rental_archive_runner.stop()

    # Same pattern for the 买卖 archive (same 14 crawled districts as
    # rental): always wired so
    # /archive/sale/status answers while disabled; own thread, own SQLite.
    app.state.crm_sale_archive_runner = SaleArchiveRunner(
        resolved,
        app.state.crm_connector_service,
        SaleArchiveStore(resolved.sale_archive_db_path),
    )
    if resolved.sale_archive_enabled:
        app.state.crm_sale_archive_runner.start()

        @app.on_event("shutdown")
        def stop_sale_archive_runner() -> None:
            app.state.crm_sale_archive_runner.stop()

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    static_dir = Path(__file__).resolve().parent / "static"
    dashboard = static_dir / "archive.html"
    login_page = static_dir / "login.html"

    @app.get("/archive", include_in_schema=False)
    def rental_archive_dashboard() -> FileResponse:
        return FileResponse(
            dashboard,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/login", include_in_schema=False)
    def qr_login_page() -> FileResponse:
        return FileResponse(
            login_page,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    return app
