"""Service-mode session watchdog: keepalive + automatic QR re-login.

The ``crm-authd serve`` process owns a ``KeepaliveTimer``, but the uvicorn
FastAPI app does not: while it runs, credentials can silently go stale (the
sliding ``lianjia_ssid`` window expires, or another browser scan supersedes
the session upstream) with nobody probing or re-triggering a login. This
watchdog closes that gap for the service process:

- Every check interval it calls ``run_keepalive`` on the session provider,
  which probes the identity endpoint, extends the ssid window, and refreshes
  via the TGC when the probe fails. Service mode polls more aggressively
  than ``crm-authd serve`` (60 s vs 1500 s) so a revoked session surfaces
  quickly.
- When the provider reports ``auth_required`` and no QR attempt is pending,
  it starts a QR login so the native scan window reappears without human
  intervention. A conflict (already ready, or an attempt already pending)
  is expected and ignored.
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
    """Background thread keeping the service session alive and logged in."""

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
        # Probe + extend the ssid window; a failed probe leads to a TGC
        # refresh inside run_keepalive, and to auth_required when that fails.
        self._session_provider.run_keepalive()
        status = self._session_provider.status()
        # Heartbeat: the service previously ran with zero keepalive
        # visibility, which is exactly how a stale session went unnoticed.
        # One INFO line per tick keeps the probe observable in the logs.
        logger.info("session_watchdog.tick state=%s", status.state.value)
        if self._qr_manager is None:
            return
        if status.state is not ConnectionState.AUTH_REQUIRED:
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
