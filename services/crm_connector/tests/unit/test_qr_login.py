from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.application.qr_login import (
    QrLoginManager,
    QrLoginState,
    _CaptureRenderer,
    auto_start_login,
)
from app.domain.errors import QrLoginConflictError, QrLoginNotFoundError
from app.domain.models import ConnectionState, ProviderStatus
from app.domain.providers.credential_bootstrap_provider import BootstrapResult
from app.domain.providers.credential_store import ActiveCredential
from app.infrastructure.settings import Settings


def _settings(**overrides: Any) -> Settings:
    merged = dict(
        bound_employee_principal="",
        upstream_profile="kecom-prod",
        request_timeout_seconds=2.0,
        credential_store_path="./run/fixture.bin",
        bootstrap_poll_interval_seconds=0.0,
        bootstrap_poll_timeout_seconds=5.0,
        bootstrap_qrcode_refresh_initial_delay_seconds=0.0,
    )
    merged.update(overrides)
    return Settings(**merged)


class FakeSession:
    """Stand-in for the credential installer used by QrLoginManager."""

    def __init__(self, *, state: ConnectionState = ConnectionState.AUTH_REQUIRED) -> None:
        self._state = state
        self.installed: list[BootstrapResult] = []

    def status(self) -> ProviderStatus:
        return ProviderStatus(self._state, "fake status")

    def make_ready(self) -> None:
        self._state = ConnectionState.READY

    def install_fresh_credential(
        self, result: BootstrapResult, *, session_id: str | None = None
    ) -> ActiveCredential:
        self.installed.append(result)
        return ActiveCredential(
            session_id=session_id or "sess-1",
            employee_principal="1000000031696069",
            credential_material=result.credential_material,
            expires_at=result.expires_at,
            credential_version=result.credential_version,
            refresh_material=result.refresh_material,
        )


class FakeBootstrap:
    """Deterministic bootstrap provider controllable from tests."""

    def __init__(
        self,
        renderer: Any,
        *,
        payload: str = "https://t.lianjia.com/TESTQR",
        result: BootstrapResult | None = None,
        error: Exception | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.renderer = renderer
        self.payload = payload
        self.result = result
        self.error = error
        self.gate = gate
        self.closed = False

    def bootstrap(self) -> BootstrapResult:
        if self.gate is not None:
            self.gate.wait(timeout=10)
        self.renderer.render(self.payload, note="scan me")
        if self.error is not None:
            raise self.error
        return self.result or BootstrapResult(
            credential_material=b'{"UCID":"1000000031696069"}',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            credential_version=1,
            refresh_material=b"{}",
        )

    def close(self) -> None:
        self.closed = True


def _manager(
    session: FakeSession | None = None,
    settings: Settings | None = None,
    **bootstrap_kwargs: Any,
) -> tuple[QrLoginManager, FakeSession, FakeBootstrap]:
    session = session or FakeSession()
    settings = settings or _settings()
    bootstrap = FakeBootstrap(None, **bootstrap_kwargs)

    def factory(renderer: Any) -> FakeBootstrap:
        bootstrap.renderer = renderer
        return bootstrap

    # Hold the manager in a list so the renderer factory can late-bind to it;
    # tests must never open the native popup.
    holder: list[QrLoginManager | None] = [None]

    def renderer_factory(login_id: str) -> Any:
        assert holder[0] is not None
        return _CaptureRenderer(holder[0], login_id)

    manager = QrLoginManager(
        settings,
        session,
        bootstrap_factory=factory,
        renderer_factory=renderer_factory,
    )
    holder[0] = manager
    return manager, session, bootstrap


def _wait_for(
    manager: QrLoginManager,
    login_id: str,
    *,
    state: str,
    timeout: float = 5.0,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(login_id)
        if status.state == state:
            return status
        time.sleep(0.01)
    return manager.status(login_id)


def test_start_returns_pending_snapshot_with_qrcode() -> None:
    gate = threading.Event()
    manager, _, bootstrap = _manager(gate=gate)
    status = manager.start()
    assert status.state == QrLoginState.PENDING.value
    assert status.login_id
    assert status.qrcode == ""
    gate.set()
    _wait_for(manager, status.login_id, state=QrLoginState.READY.value)
    assert bootstrap.closed is True
    assert bootstrap.renderer is not None


def test_success_installs_credential_and_reports_ready() -> None:
    manager, session, _ = _manager()
    status = manager.start()
    ready = _wait_for(manager, status.login_id, state=QrLoginState.READY.value)
    assert ready.qrcode == "https://t.lianjia.com/TESTQR"
    assert ready.employee_principal == "1000000031696069"
    assert ready.expires_at is not None
    assert len(session.installed) == 1
    assert manager.status(status.login_id).state == QrLoginState.READY.value


def test_bootstrap_failure_reports_failed() -> None:
    manager, session, _ = _manager(error=RuntimeError("upstream exploded"))
    status = manager.start()
    failed = _wait_for(manager, status.login_id, state=QrLoginState.FAILED.value)
    assert "upstream exploded" in failed.message
    assert session.installed == []


def test_principal_mismatch_reports_failed() -> None:
    manager, session, _ = _manager(
        settings=_settings(bound_employee_principal="expected-100"),
    )
    status = manager.start()
    failed = _wait_for(manager, status.login_id, state=QrLoginState.FAILED.value)
    assert "expected-100" in failed.message
    assert "1000000031696069" in failed.message
    assert len(session.installed) == 1  # installed before the mismatch check


def test_cancel_marks_cancelled_and_skips_install() -> None:
    gate = threading.Event()
    manager, session, _ = _manager(gate=gate)
    status = manager.start()
    cancelled = manager.cancel(status.login_id)
    assert cancelled.state == QrLoginState.CANCELLED.value
    # Release the worker; it must notice the cancel and not install.
    gate.set()
    time.sleep(0.2)
    assert session.installed == []
    assert manager.status(status.login_id).state == QrLoginState.CANCELLED.value


def test_cancel_after_finish_conflicts() -> None:
    manager, _, _ = _manager()
    status = manager.start()
    _wait_for(manager, status.login_id, state=QrLoginState.READY.value)
    with pytest.raises(QrLoginConflictError):
        manager.cancel(status.login_id)


def test_start_conflicts_when_ready() -> None:
    session = FakeSession(state=ConnectionState.READY)
    manager, _, _ = _manager(session=session)
    with pytest.raises(QrLoginConflictError, match="already authenticated"):
        manager.start()


def test_start_conflicts_when_another_pending() -> None:
    gate = threading.Event()
    manager, _, _ = _manager(gate=gate)
    first = manager.start()
    try:
        with pytest.raises(QrLoginConflictError, match="already in progress"):
            manager.start()
    finally:
        gate.set()
    _wait_for(manager, first.login_id, state=QrLoginState.READY.value)


def test_unknown_session_raises_not_found() -> None:
    manager, _, _ = _manager()
    with pytest.raises(QrLoginNotFoundError):
        manager.status("no-such-login")
    with pytest.raises(QrLoginNotFoundError):
        manager.cancel("no-such-login")
    with pytest.raises(QrLoginNotFoundError):
        manager.qrcode_png("no-such-login")


def test_qrcode_png_returns_png_bytes() -> None:
    manager, _, _ = _manager()
    status = manager.start()
    _wait_for(manager, status.login_id, state=QrLoginState.READY.value)
    png = manager.qrcode_png(status.login_id)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_qrcode_png_before_payload_conflicts() -> None:
    gate = threading.Event()
    manager, _, _ = _manager(gate=gate)
    status = manager.start()
    try:
        with pytest.raises(QrLoginConflictError, match="not ready"):
            manager.qrcode_png(status.login_id)
    finally:
        gate.set()


def test_capture_renderer_pump_raises_when_cancelled() -> None:
    manager, _, _ = _manager(gate=threading.Event())
    status = manager.start()
    renderer = _CaptureRenderer(manager, status.login_id)
    manager.cancel(status.login_id)
    with pytest.raises(RuntimeError, match="cancelled"):
        renderer.pump()


def test_needs_login_reflects_session_state() -> None:
    manager, session, _ = _manager()
    assert manager.needs_login() is True
    session.make_ready()
    assert manager.needs_login() is False


def test_needs_login_false_while_scan_pending() -> None:
    gate = threading.Event()
    manager, _, _ = _manager(gate=gate)
    first = manager.start()
    assert manager.needs_login() is False  # a scan is already pending
    gate.set()
    _wait_for(manager, first.login_id, state=QrLoginState.READY.value)
    assert manager.needs_login() is True  # pending attempt finished


def test_auto_start_skips_when_ready() -> None:
    session = FakeSession(state=ConnectionState.READY)
    manager, _, _ = _manager(session=session)
    auto_start_login(manager)
    assert manager._sessions == {}  # type: ignore[attr-defined]


def test_auto_start_starts_login_when_auth_required() -> None:
    manager, session, _ = _manager(gate=threading.Event())
    auto_start_login(manager)
    assert len(manager._sessions) == 1
    (login_id, session_record) = next(iter(manager._sessions.items()))
    assert session_record.state is QrLoginState.PENDING
    manager.cancel(login_id)
    assert session.installed == []


def test_auto_start_ignores_pending_conflict() -> None:
    gate = threading.Event()
    manager, _, _ = _manager(gate=gate)
    first = manager.start()
    auto_start_login(manager)  # must not raise despite a pending attempt
    status = manager.status(first.login_id)
    assert status.state == QrLoginState.PENDING.value
    manager.cancel(first.login_id)


def test_prune_removes_finished_sessions() -> None:
    now = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    call_count = {"n": 0}

    def clock() -> datetime:
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return now
        return now + timedelta(seconds=901)

    manager, _, _ = _manager()
    manager._clock = clock  # type: ignore[attr-defined]
    first = manager.start()
    _wait_for(manager, first.login_id, state=QrLoginState.READY.value)
    assert len(manager._sessions) == 1

    gate = threading.Event()
    manager._bootstrap_factory = lambda renderer: FakeBootstrap(  # type: ignore[method-assign]
        renderer, gate=gate
    )
    manager.start()  # prunes the finished session, starts a blocked attempt
    with pytest.raises(QrLoginNotFoundError):
        manager.status(first.login_id)
    gate.set()
