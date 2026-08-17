from __future__ import annotations

import argparse
import logging
import sys
import threading
from datetime import UTC, datetime
from typing import Iterable

import uvicorn

from app.domain.models import ConnectionState
from app.infrastructure.kecom_qr_bootstrap import KeComQrBootstrapProvider
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings, load_settings
from app.infrastructure.windows_dpapi_credential_store import WindowsDpapiCredentialStore
from app.authd.server import build_app

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


def cmd_login(settings: Settings) -> int:
    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    bootstrap = KeComQrBootstrapProvider(settings)
    session = KecomSessionProvider(settings, store, bootstrap)

    print(f"[*] bound_employee_principal = {settings.bound_employee_principal or '<unset>'}")
    print(f"[*] credential_store_path = {settings.credential_store_path}")
    print("[*] bootstrapping via login.ke.com qrcode SSO ...")

    # bootstrap blocks here until the bound employee has scanned + confirmed
    # on their phone. On Windows it owns a native QR dialog which closes
    # automatically after the phone confirms the scan.
    try:
        result = bootstrap.bootstrap()
    except Exception as exc:  # pragma: no cover - terminal error path
        print(f"[!] bootstrap failed: {exc}", file=sys.stderr)
        return 2

    active = session.install_fresh_credential(result)
    _assert_principal_matches(settings, active.employee_principal)
    print("[*] login successful:")
    print(f"    principal        = {active.employee_principal}")
    print(f"    session_id       = {active.session_id}")
    print(f"    expires_at       = {active.expires_at.isoformat() if active.expires_at else '<unknown>'}")
    print(f"    version          = {active.credential_version}")
    # Only crm-authd knows the refresh material existed at all; we never
    # surface the underlying cookie jar.
    print("[*] refresh_material captured" if active.refresh_material else "[*] refresh_material absent")
    return 0


def cmd_status(settings: Settings) -> int:
    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    active = store.load_active()
    if active is None:
        print(f"state = {ConnectionState.AUTH_REQUIRED.value}")
        print("message = CRM authorization not bootstrapped; run `crm-authd login`")
        return 1
    state = ConnectionState.EXPIRING if _is_expiring(active) else ConnectionState.READY
    print(f"state = {state.value}")
    print(f"bound_employee_principal = {settings.bound_employee_principal or '<unset>'}")
    print(f"session_principal = {active.employee_principal}")
    print(f"session_id = {active.session_id}")
    print(f"expires_at = {active.expires_at.isoformat() if active.expires_at else '<unknown>'}")
    print(f"credential_version = {active.credential_version}")
    print(f"refresh_material = {'present' if active.refresh_material else 'absent'}")
    return 0


def cmd_logout(settings: Settings) -> int:
    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    active = store.load_active()
    if active is None:
        print("state = auth_required  (no stored credential)")
        return 1
    bootstrap = KeComQrBootstrapProvider(settings)
    try:
        bootstrap.revoke(active)
    except Exception as exc:  # pragma: no cover - terminal error path
        logger.warning("revoke failed: %s", exc)
    store.invalidate(active.session_id, "upstream_rejected")
    print("state = auth_required")
    print("message = credential revoked and local record cleared")
    return 0


def cmd_serve(settings: Settings) -> int:
    store = WindowsDpapiCredentialStore(settings.credential_store_path)
    bootstrap = KeComQrBootstrapProvider(settings)
    session = KecomSessionProvider(settings, store, bootstrap)

    # Lazy validation by default: no timer probes the upstream while idle.
    # An explicit CC_KEEPALIVE_INTERVAL_SECONDS > 0 opts back into the
    # classic periodic keepalive thread.
    timer: KeepaliveTimer | None = None
    if settings.refresh_keepalive_interval_seconds > 0:
        timer = KeepaliveTimer(session, settings.refresh_keepalive_interval_seconds)
        timer.start()

    app = build_app(settings, session)
    host, _, port = settings.authd_listen_address.rpartition(":")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port) if port else 8021, log_level="info")
    if timer is not None:
        timer.stop()
    return 0


# -- helpers --------------------------------------------------------------


def _is_expiring(active) -> bool:
    if active.expires_at is None:
        return False
    now = datetime.now(UTC)
    return active.expires_at <= now or (active.expires_at - now).total_seconds() < 60


def _assert_principal_matches(settings: Settings, principal: str) -> None:
    if not settings.bound_employee_principal:
        return
    if principal != settings.bound_employee_principal:
        raise SystemExit(
            f"bootstrap principal mismatch: connector bound to "
            f"{settings.bound_employee_principal}, scanned as {principal}"
        )


class KeepaliveTimer:
    """Background thread that calls ``run_keepalive`` on a fixed interval.

    Lives only in the ``crm-authd serve`` process. The Connector's FastAPI
    app interposes the SessionProvider directly, so the timer does not need
    to coordinate with request handling.
    """

    def __init__(self, session: KecomSessionProvider, interval_seconds: float) -> None:
        self._session = session
        self._interval = max(interval_seconds, 60.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="crm-authd-keepalive", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:  # pragma: no cover - exercised under serve
        while not self._stop.wait(self._interval):
            try:
                self._session.run_keepalive()
            except Exception as exc:
                logger.warning("keepalive.unhandled error: %s", exc)


COMMANDS = {
    "login": cmd_login,
    "status": cmd_status,
    "logout": cmd_logout,
    "serve": cmd_serve,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crm-authd")
    parser.add_argument("command", choices=sorted(COMMANDS))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = load_settings()
    handler = COMMANDS[args.command]
    return int(handler(settings) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
