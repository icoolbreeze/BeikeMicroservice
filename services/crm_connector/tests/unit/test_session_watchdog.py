from __future__ import annotations



from app.application.session_watchdog import SessionWatchdog
from app.domain.errors import QrLoginConflictError
from app.domain.models import ConnectionState, ProviderStatus


class FakeProvider:
    """Provider whose status is observable from tests. The keepalive counter
    stays as a regression guard: the lazy watchdog must never call it."""

    def __init__(
        self,
        *,
        state: ConnectionState = ConnectionState.READY,
        status_error: Exception | None = None,
    ) -> None:
        self._state = state
        self._status_error = status_error
        self.keepalive_calls = 0

    def status(self) -> ProviderStatus:
        if self._status_error is not None:
            raise self._status_error
        return ProviderStatus(self._state, "fake status")

    def run_keepalive(self) -> None:
        self.keepalive_calls += 1

    def make_auth_required(self) -> None:
        self._state = ConnectionState.AUTH_REQUIRED


class FakeQrManager:
    """Models QrLoginManager semantics: a successful start leaves a scan
    pending, so needs_login() turns False until that scan finishes."""

    def __init__(self, *, needs_login: bool = True, start_error: Exception | None = None) -> None:
        self._needs_login = needs_login
        self._start_error = start_error
        self._scan_pending = False
        self.start_calls = 0

    def needs_login(self) -> bool:
        return self._needs_login and not self._scan_pending

    def start(self) -> object:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        self._scan_pending = True
        return {"state": "pending"}


class FakeStopEvent:
    """Event whose wait() lets exactly one tick through, then stops the loop."""

    def __init__(self) -> None:
        self._waits = 0

    def wait(self, timeout: float) -> bool:
        self._waits += 1
        return self._waits > 1

    def set(self) -> None:
        pass

    def is_set(self) -> bool:
        return True


def _watchdog(
    provider: FakeProvider | None = None,
    qr_manager: FakeQrManager | None = None,
) -> tuple[SessionWatchdog, FakeProvider, FakeQrManager]:
    provider = provider or FakeProvider()
    qr_manager = qr_manager or FakeQrManager()
    watchdog = SessionWatchdog(provider, qr_manager, check_interval_seconds=5.0)
    watchdog._stop = FakeStopEvent()  # type: ignore[attr-defined]
    return watchdog, provider, qr_manager


def test_tick_never_probes_the_upstream() -> None:
    # Lazy validation: the watchdog only reads local state; probing is a
    # keepalive-timer / explicit-endpoint concern.
    watchdog, provider, qr_manager = _watchdog()
    watchdog._tick()
    watchdog._tick()
    assert provider.keepalive_calls == 0
    assert qr_manager.start_calls == 0  # session is ready, nothing to re-login


def test_tick_skips_qr_login_when_ready() -> None:
    watchdog, provider, qr_manager = _watchdog()
    watchdog._tick()
    assert qr_manager.start_calls == 0


def test_tick_relogs_when_auth_required_and_no_scan_pending() -> None:
    watchdog, provider, qr_manager = _watchdog()
    provider.make_auth_required()
    watchdog._tick()
    assert qr_manager.start_calls == 1
    # A second tick must not spam another QR window while one is pending.
    watchdog._tick()
    assert qr_manager.start_calls == 1


def test_tick_relogs_when_degraded_and_no_scan_pending() -> None:
    watchdog, provider, qr_manager = _watchdog(
        provider=FakeProvider(state=ConnectionState.DEGRADED)
    )

    watchdog._tick()

    assert qr_manager.start_calls == 1


def test_tick_skips_qr_login_when_scan_pending() -> None:
    watchdog, provider, qr_manager = _watchdog(
        qr_manager=FakeQrManager(needs_login=False)
    )
    provider.make_auth_required()
    watchdog._tick()
    assert qr_manager.start_calls == 0


def test_tick_ignores_qr_start_conflict() -> None:
    # A race where another flow already opened a QR session must not crash
    # the watchdog — the conflict is expected and logged, then ignored.
    watchdog, provider, qr_manager = _watchdog(
        qr_manager=FakeQrManager(start_error=QrLoginConflictError("already in progress"))
    )
    provider.make_auth_required()
    watchdog._tick()  # must not raise
    assert qr_manager.start_calls == 1


def test_tick_without_qr_manager_is_safe() -> None:
    watchdog = SessionWatchdog(FakeProvider(state=ConnectionState.AUTH_REQUIRED))
    watchdog._stop = FakeStopEvent()  # type: ignore[attr-defined]
    watchdog._tick()  # must not raise with qr_manager=None
    assert watchdog._session_provider.keepalive_calls == 0  # type: ignore[attr-defined]


def test_run_survives_status_errors() -> None:
    # A crashed status read (e.g. store I/O failure) must not kill the
    # loop: the next tick reads again.
    watchdog, provider, qr_manager = _watchdog(
        provider=FakeProvider(status_error=RuntimeError("store exploded"))
    )
    watchdog._run()  # must not raise
    assert qr_manager.start_calls == 0


def test_stop_terminates_thread() -> None:
    watchdog = SessionWatchdog(FakeProvider(), check_interval_seconds=60.0)
    watchdog.start()
    watchdog.stop()
    assert not watchdog._thread.is_alive()
