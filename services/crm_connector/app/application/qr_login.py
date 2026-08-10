"""QR-code login orchestration for the running connector service.

The CLI flow (``crm-authd login``) blocks in the terminal and owns a native
QR dialog. This module exposes the same human-assisted bootstrap over the
service's HTTP surface: ``POST /api/v1/auth/login`` starts a login session in
a background thread, the caller renders/relays the QR payload to the employee,
and ``GET /api/v1/auth/login/{id}`` reports the scan state until the fresh
credential is installed and the session becomes READY.

The bootstrap provider is created per login attempt (fresh httpx client jar,
fresh QR code) so concurrent or repeated attempts never mix TGC cookies.
Only one attempt may be pending at a time; a second ``start()`` raises
``QrLoginConflictError``.
"""

from __future__ import annotations

import io
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Protocol

from app.domain.errors import (
    QrLoginConflictError,
    QrLoginNotFoundError,
)
from app.domain.models import ConnectionState, ProviderStatus
from app.domain.providers.credential_bootstrap_provider import (
    BootstrapResult,
    CredentialBootstrapProvider,
)
from app.domain.providers.credential_store import ActiveCredential
from app.infrastructure.kecom_qr_bootstrap import (
    KeComQrBootstrapProvider,
    _default_renderer,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)


class CredentialInstaller(Protocol):
    """The subset of the session provider the QR login needs.

    Narrower than ``SessionProvider`` so the manager can stay independent of
    the full request path while still installing freshly bootstrapped
    credentials and observing connection state.
    """

    def status(self) -> ProviderStatus:
        """Return the current authorization/network state."""

    def install_fresh_credential(
        self,
        result: BootstrapResult,
        *,
        session_id: str | None = None,
    ) -> ActiveCredential:
        """Persist a freshly bootstrapped credential and make it active."""


class QrLoginState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class QrLoginStatus:
    """Immutable snapshot of a login session for API responses."""

    login_id: str
    state: str
    qrcode: str
    note: str
    message: str
    employee_principal: str | None = None
    expires_at: datetime | None = None


@dataclass
class _QrLoginSession:
    login_id: str
    created_at: datetime
    state: QrLoginState = QrLoginState.PENDING
    qrcode: str = ""
    note: str = ""
    message: str = ""
    employee_principal: str | None = None
    expires_at: datetime | None = None
    cancelled: bool = False
    finished_at: datetime | None = None


class _LoginCancelled(RuntimeError):
    """Raised from the renderer pump to abort a background bootstrap."""


class _CaptureRenderer:
    """Renderer that records QR payloads instead of drawing a window.

    ``render`` stores the payload on the owning session; ``pump`` checks the
    cancellation flag so a user-initiated cancel aborts the bootstrap loop
    within one poll interval instead of waiting for the full timeout.
    """

    def __init__(self, manager: "QrLoginManager", login_id: str) -> None:
        self._manager = manager
        self._login_id = login_id

    def render(self, payload: str, *, note: str) -> None:
        with self._manager._lock:  # noqa: SLF001 - same package boundary
            session = self._manager._sessions.get(self._login_id)  # noqa: SLF001
            if session is not None:
                session.qrcode = payload
                session.note = note

    def pump(self) -> None:
        with self._manager._lock:  # noqa: SLF001
            session = self._manager._sessions.get(self._login_id)  # noqa: SLF001
            if session is not None and session.cancelled:
                raise _LoginCancelled("login cancelled by user")

    def close(self) -> None:
        return None


class _PopupAndCaptureRenderer:
    """Native QR popup (Windows Tk window) that also records payloads.

    This is the default renderer for service-side logins: the employee scans
    the popup window directly, no browser required. The captured payload keeps
    the ``/api/v1/auth/login`` status and ``qrcode.png`` endpoints working as
    an alternative view. All Tk calls happen in the bootstrap worker thread,
    exactly like ``crm-authd login`` does on the CLI thread.
    """

    def __init__(self, manager: "QrLoginManager", login_id: str) -> None:
        self._manager = manager
        self._login_id = login_id
        self._inner = _default_renderer(manager._settings)  # noqa: SLF001

    def render(self, payload: str, *, note: str) -> None:
        with self._manager._lock:  # noqa: SLF001
            session = self._manager._sessions.get(self._login_id)  # noqa: SLF001
            if session is not None:
                session.qrcode = payload
                session.note = note
        self._inner.render(payload, note=note)

    def pump(self) -> None:
        with self._manager._lock:  # noqa: SLF001
            session = self._manager._sessions.get(self._login_id)  # noqa: SLF001
            if session is not None and session.cancelled:
                raise _LoginCancelled("login cancelled by user")
        pump = getattr(self._inner, "pump", None)
        if callable(pump):
            pump()

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class QrLoginManager:
    """Owns in-flight QR login attempts and installs fresh credentials."""

    # Finished sessions are pruned once older than this; keep the window large
    # enough for a slow employee to open a result page after scanning.
    _PRUNE_AFTER_SECONDS = 600.0
    _MAX_RETAINED = 8

    def __init__(
        self,
        settings: Settings,
        session_provider: CredentialInstaller,
        *,
        bootstrap_factory: (
            Callable[[Any], CredentialBootstrapProvider] | None
        ) = None,
        renderer_factory: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._session_provider = session_provider
        self._bootstrap_factory = bootstrap_factory or (
            lambda renderer: KeComQrBootstrapProvider(settings, qr_renderer=renderer)
        )
        # Default renderer is the native QR popup; the capture-only renderer
        # stays available for tests and headless hosts.
        self._renderer_factory = renderer_factory or (
            lambda login_id: _PopupAndCaptureRenderer(self, login_id)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._sessions: dict[str, _QrLoginSession] = {}

    # -- public API ---------------------------------------------------------

    def start(self) -> QrLoginStatus:
        """Begin a QR login attempt and return the first QR snapshot.

        Refuses to start when the session is already authenticated or another
        attempt is still pending.
        """
        with self._lock:
            status = self._session_provider.status()
            if status.state is ConnectionState.READY:
                raise QrLoginConflictError(
                    "CRM session already authenticated; no login required"
                )
            self._prune_locked()
            if any(
                session.state is QrLoginState.PENDING
                for session in self._sessions.values()
            ):
                raise QrLoginConflictError(
                    "a QR login attempt is already in progress; poll it or cancel it first"
                )
            session = _QrLoginSession(
                login_id=str(uuid.uuid4()),
                created_at=self._clock(),
            )
            self._sessions[session.login_id] = session

        thread = threading.Thread(
            target=self._worker,
            args=(session.login_id,),
            name=f"crm-qr-login-{session.login_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.status(session.login_id)

    def status(self, login_id: str) -> QrLoginStatus:
        with self._lock:
            session = self._session_or_raise(login_id)
            return self._snapshot(session)

    def needs_login(self) -> bool:
        """True when the CRM session requires a human QR scan right now.

        False when a scan attempt is already pending — callers (the session
        watchdog, auto-start) use this to avoid spamming duplicate windows.
        """
        with self._lock:
            if (
                self._session_provider.status().state
                is not ConnectionState.AUTH_REQUIRED
            ):
                return False
            self._prune_locked()
            return not any(
                session.state is QrLoginState.PENDING
                for session in self._sessions.values()
            )

    def qrcode_png(self, login_id: str) -> bytes:
        """Render the session's latest QR payload to PNG bytes."""
        with self._lock:
            session = self._session_or_raise(login_id)
            payload = session.qrcode
        if not payload:
            raise QrLoginConflictError("QR code is not ready yet; retry shortly")
        try:
            import qrcode  # type: ignore[import-untyped]

            image = qrcode.make(payload)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as exc:  # pragma: no cover - optional imaging stack
            logger.warning("qr_login.png_failed class=%s", exc.__class__.__name__)
            raise QrLoginConflictError("QR image rendering is unavailable") from exc

    def cancel(self, login_id: str) -> QrLoginStatus:
        """Ask the background bootstrap to stop; aborts within one poll tick."""
        with self._lock:
            session = self._session_or_raise(login_id)
            if session.state is not QrLoginState.PENDING:
                raise QrLoginConflictError(
                    f"login attempt already finished ({session.state.value})"
                )
            session.state = QrLoginState.CANCELLED
            session.cancelled = True
            session.message = "login cancelled by user"
            session.finished_at = self._clock()
            return self._snapshot(session)

    # -- worker -------------------------------------------------------------

    def _worker(self, login_id: str) -> None:
        renderer = self._renderer_factory(login_id)
        bootstrap: CredentialBootstrapProvider | None = None
        try:
            bootstrap = self._bootstrap_factory(renderer)
            result = bootstrap.bootstrap()
            with self._lock:
                session = self._sessions.get(login_id)
                if session is None or session.cancelled:
                    return
            active = self._session_provider.install_fresh_credential(result)
            expected = self._settings.bound_employee_principal
            if expected and active.employee_principal != expected:
                raise QrLoginConflictError(
                    f"bootstrap principal mismatch: connector bound to {expected}, "
                    f"scanned as {active.employee_principal}"
                )
            self._finish(
                login_id,
                QrLoginState.READY,
                "login succeeded; CRM session is ready",
                principal=active.employee_principal,
                expires_at=active.expires_at,
            )
        except _LoginCancelled as exc:
            self._finish(login_id, QrLoginState.CANCELLED, str(exc))
        except Exception as exc:
            logger.warning("qr_login.worker_failed login_id=%s class=%s", login_id, exc.__class__.__name__)
            self._finish(
                login_id,
                QrLoginState.FAILED,
                f"QR login failed: {exc}",
            )
        finally:
            close = getattr(bootstrap, "close", None)
            if callable(close):
                close()

    def _finish(
        self,
        login_id: str,
        state: QrLoginState,
        message: str,
        *,
        principal: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        with self._lock:
            session = self._sessions.get(login_id)
            if session is None:
                return
            session.state = state
            session.message = message
            session.employee_principal = principal
            session.expires_at = expires_at
            session.finished_at = self._clock()

    # -- internals ----------------------------------------------------------

    def _session_or_raise(self, login_id: str) -> _QrLoginSession:
        session = self._sessions.get(login_id)
        if session is None:
            raise QrLoginNotFoundError(f"unknown QR login session: {login_id}")
        return session

    def _snapshot(self, session: _QrLoginSession) -> QrLoginStatus:
        return QrLoginStatus(
            login_id=session.login_id,
            state=session.state.value,
            qrcode=session.qrcode,
            note=session.note,
            message=session.message,
            employee_principal=session.employee_principal,
            expires_at=session.expires_at,
        )

    def _prune_locked(self) -> None:
        now = self._clock()
        expired = [
            login_id
            for login_id, session in self._sessions.items()
            if session.finished_at is not None
            and (now - session.finished_at).total_seconds() > self._PRUNE_AFTER_SECONDS
        ]
        for login_id in expired:
            self._sessions.pop(login_id, None)
        if len(self._sessions) > self._MAX_RETAINED:
            for login_id in list(self._sessions)[: len(self._sessions) - self._MAX_RETAINED]:
                self._sessions.pop(login_id, None)


def auto_start_login(manager: QrLoginManager) -> None:
    """Best-effort QR login kickoff used at service startup.

    Runs in a background thread: when the session is ``auth_required`` it
    starts a login attempt (which pops the native QR window by default) so
    the employee can scan without touching a browser or API. Conflicts
    (already ready or an attempt already pending) are expected and ignored.
    """
    try:
        if manager.needs_login():
            manager.start()
    except QrLoginConflictError:
        pass
    except Exception as exc:  # pragma: no cover - defensive startup path
        logger.warning("qr_login.auto_start_failed class=%s", exc.__class__.__name__)
