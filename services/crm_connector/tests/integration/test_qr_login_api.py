from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.application.qr_login import QrLoginManager
from app.domain.models import ConnectionState, ProviderStatus
from app.domain.providers.credential_bootstrap_provider import BootstrapResult
from app.domain.providers.credential_store import ActiveCredential
from app.infrastructure.settings import Settings
from app.main import create_app


class FakeSession:
    def __init__(self) -> None:
        self.installed: list[BootstrapResult] = []

    def status(self) -> ProviderStatus:
        return ProviderStatus(ConnectionState.AUTH_REQUIRED, "fake")

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
    def __init__(self, renderer: Any, *, gate: threading.Event | None = None) -> None:
        self.renderer = renderer
        self.gate = gate

    def bootstrap(self) -> BootstrapResult:
        if self.gate is not None:
            self.gate.wait(timeout=10)
        self.renderer.render("https://t.lianjia.com/APITEST", note="scan me")
        return BootstrapResult(
            credential_material=b'{"UCID":"1000000031696069"}',
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            credential_version=1,
            refresh_material=b"{}",
        )

    def close(self) -> None:
        return None


def _wired_app(
    tmp_path,
    *,
    gate: threading.Event | None = None,
    unconfigured: bool = False,
) -> tuple[TestClient, FakeSession]:
    settings = Settings(
        upstream_profile="unconfigured" if unconfigured else "kecom-prod",
        credential_store_path=str(tmp_path / "cred.bin"),
        qr_login_auto_start=False,
    )
    app = create_app(settings)
    if not unconfigured:
        session = FakeSession()
        bootstrap = FakeBootstrap(None, gate=gate)

        def factory(renderer: Any) -> FakeBootstrap:
            bootstrap.renderer = renderer
            return bootstrap

        manager = QrLoginManager(settings, session, bootstrap_factory=factory)
        app.state.crm_qr_login_manager = manager
        return TestClient(app), session
    return TestClient(app), FakeSession()


def _poll_until(client: TestClient, login_id: str, state: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/auth/login/{login_id}").json()
        if body["state"] == state:
            return body
        time.sleep(0.01)
    return client.get(f"/api/v1/auth/login/{login_id}").json()


def test_full_qr_login_flow_over_http(tmp_path) -> None:
    gate = threading.Event()
    client, session = _wired_app(tmp_path, gate=gate)

    response = client.post("/api/v1/auth/login")
    assert response.status_code == 200
    start = response.json()
    assert start["state"] == "pending"
    assert start["login_id"]

    gate.set()
    ready = _poll_until(client, start["login_id"], "ready")
    assert ready["qrcode"] == "https://t.lianjia.com/APITEST"
    assert ready["employee_principal"] == "1000000031696069"
    assert ready["expires_at"] is not None
    assert len(session.installed) == 1

    png = client.get(f"/api/v1/auth/login/{start['login_id']}/qrcode.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_login_conflicts_while_pending(tmp_path) -> None:
    client, _ = _wired_app(tmp_path, gate=threading.Event())
    first = client.post("/api/v1/auth/login")
    assert first.status_code == 200
    second = client.post("/api/v1/auth/login")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "CRM_QR_LOGIN_CONFLICT"
    client.post(f"/api/v1/auth/login/{first.json()['login_id']}/cancel")


def test_cancel_login_over_http(tmp_path) -> None:
    client, session = _wired_app(tmp_path, gate=threading.Event())
    start = client.post("/api/v1/auth/login").json()
    cancelled = client.post(f"/api/v1/auth/login/{start['login_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    status = client.get(f"/api/v1/auth/login/{start['login_id']}")
    assert status.json()["state"] == "cancelled"
    # Cancelling again must conflict.
    assert (
        client.post(f"/api/v1/auth/login/{start['login_id']}/cancel").status_code == 409
    )


def test_unknown_login_returns_404(tmp_path) -> None:
    client, _ = _wired_app(tmp_path)
    response = client.get("/api/v1/auth/login/no-such-login")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CRM_QR_LOGIN_NOT_FOUND"


def test_login_unavailable_on_unconfigured_profile(tmp_path) -> None:
    client, _ = _wired_app(tmp_path, unconfigured=True)
    response = client.post("/api/v1/auth/login")
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "CRM_UPSTREAM_NOT_CONFIGURED"
