"""Service-mode session watchdog: local-state monitor + automatic QR re-login.

Lazy-validation design: nothing probes the upstream on a timer anymore.
Session liveness is proven by real business calls (each success rolls the
local expiry estimate forward inside the session provider) and recovered
by the silent TGC renewal on the next request. This watchdog therefore
only reads local provider state:

- Every check interval it reads ``status()`` — a purely local read that
  never touches the upstream, so an idle service emits zero upstream
  traffic.
- When the provider reports ``auth_required`` (a real call failed and the
  TGC renewal could not recover it) and no QR attempt is pending, it
  starts a QR login so the native scan window reappears without human
  intervention. A conflict (already ready, or an attempt already pending)
  is expected and ignored.

Active periodic probing remains available as an opt-in: set
``CC_KEEPALIVE_INTERVAL_SECONDS > 0`` for the ``crm-authd serve``
KeepaliveTimer, or call ``GET /api/v1/auth/keepalive`` on demand.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from app.domain.models import ConnectionState
from app.domain.providers.session_provider import SessionProvider

logger = logging.getLogger(__name__)


class QrLoginManagerProtocol(Protocol):
    """The subset of ``QrLoginManager`` the watchdog relies on."""

    def needs_login(self) -> bool: ...

    def start(self) -> object: ...


class SessionWatchdog:
    """Background thread re-triggering QR login when the session dies.

    Monitors local provider state only; it never probes the upstream
    itself (lazy validation keeps idle time request-free).
    """

    def __init__(
        self,
        session_provider: SessionProvider,
        qr_manager: QrLoginManagerProtocol | None = None,
        *,
        check_interval_seconds: float = 60.0,
    ) -> None:
        self._session_provider = session_provider
        self._qr_manager = qr_manager
        self._interval = max(check_interval_seconds, 5.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="crm-session-watchdog", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:  # pragma: no cover - exercised under the service
        while not self._stop.wait(self._interval):
            try:
                self._tick()
            except Exception as exc:
                logger.warning("session_watchdog.tick_failed class=%s", exc.__class__.__name__)

    def _tick(self) -> None:
        # Local read only: status() does not probe the upstream. auth_required
        # here means a real call already failed and the silent TGC renewal
        # could not recover it, so a human rescan is genuinely needed.
        status = self._session_provider.status()
        # Heartbeat: one INFO line per tick keeps the monitor observable in
        # the logs without generating any upstream traffic.
        logger.info("session_watchdog.tick state=%s", status.state.value)
        if self._qr_manager is None:
            return
        if status.state not in (ConnectionState.AUTH_REQUIRED, ConnectionState.DEGRADED):
            return
        if not self._qr_manager.needs_login():
            return
        try:
            self._qr_manager.start()
            logger.info(
                "session_watchdog.relogin_started credential stale; QR login window opened"
            )
        except Exception as exc:  # noqa: BLE001 - conflict/noop paths are expected
            logger.warning(
                "session_watchdog.relogin_skipped class=%s", exc.__class__.__name__
            )
