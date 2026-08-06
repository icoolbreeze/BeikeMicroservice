from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI

from app.domain.models import ConnectionState
from app.domain.providers.credential_store import CredentialStore
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth")


@dataclass(frozen=True)
class AuthStatusResponse:
    state: str
    bound_employee_principal: str | None
    expires_at: str | None
    last_keepalive_at: str | None
    message: str

    @classmethod
    def from_provider(
        cls,
        provider: KecomSessionProvider,
        settings: Settings,
    ) -> "AuthStatusResponse":
        status = provider.status()
        with provider._lock:  # noqa: SLF001 - same package boundary
            active = provider._active  # noqa: SLF001
        return cls(
            state=status.state.value,
            bound_employee_principal=settings.bound_employee_principal or None,
            expires_at=active.expires_at.isoformat() if active and active.expires_at else None,
            last_keepalive_at=(
                provider._last_keepalive_at.isoformat()  # noqa: SLF001
                if provider._last_keepalive_at is not None
                else None
            ),
            message=status.message,
        )


def build_app(
    settings: Settings,
    session_provider: KecomSessionProvider,
) -> FastAPI:
    app = FastAPI(
        title="crm-authd",
        description="Local CRM Connector authorization center; never discloses credentials.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.session_provider = session_provider

    @router.get("/status", response_model=AuthStatusResponse)
    def auth_status() -> AuthStatusResponse:
        provider: KecomSessionProvider = app.state.session_provider
        return AuthStatusResponse.from_provider(provider, app.state.settings)

    # The poll endpoint is intended for future interactive UIs (system tray
    # app, localhost page). It mirrors ``status`` today; we keep a separate
    # path so the auth center can later add SSE without breaking callers.
    @router.get("/poll", response_model=AuthStatusResponse)
    def auth_poll() -> AuthStatusResponse:
        provider: KecomSessionProvider = app.state.session_provider
        return AuthStatusResponse.from_provider(provider, app.state.settings)

    @router.get("/keepalive")
    def trigger_keepalive() -> dict[str, str]:
        provider: KecomSessionProvider = app.state.session_provider
        status = provider.run_keepalive()
        return {"state": status.state.value, "message": status.message}

    # ``notify`` is reserved for the future tray UI to push notifications
    # *into* crm-authd (e.g. "user clicked 'rescan'"). For now it mirrors
    # status; later it will accept a JSON body. Returning 200 here keeps
    # the API stable.
    @router.get("/notify")
    def auth_notify() -> AuthStatusResponse:
        provider: KecomSessionProvider = app.state.session_provider
        return AuthStatusResponse.from_provider(provider, app.state.settings)

    app.include_router(router)
    return app


def connection_state_from_provider(provider: KecomSessionProvider) -> ConnectionState:
    return provider.status().state


def credential_store_for(provider: KecomSessionProvider) -> CredentialStore:  # pragma: no cover
    """Convenience accessor used by tests; not called in production code paths."""
    return provider._store  # noqa: SLF001
