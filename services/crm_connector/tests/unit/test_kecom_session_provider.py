from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.domain.models import ConnectionState, Principal
from app.domain.providers.credential_bootstrap_provider import BootstrapResult
from app.domain.providers.credential_store import ActiveCredential
from app.domain.providers.session_provider import AuthorizedRequest
from app.infrastructure.kecom_session_provider import KecomSessionProvider
from app.infrastructure.settings import Settings

# Fixed clock so expires_at arithmetic is fully deterministic.
NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self, credential: ActiveCredential | None = None) -> None:
        self._slot = credential
        self.saved: list[ActiveCredential] = []
        self.invalidated: list[tuple[str, str]] = []

    def save(self, credential: ActiveCredential) -> None:
        self.saved.append(credential)
        self._slot = credential

    def load_active(self) -> ActiveCredential | None:
        return self._slot

    def invalidate(self, session_id: str, reason: str) -> None:
        self.invalidated.append((session_id, reason))
        if self._slot is not None and self._slot.session_id == session_id:
            self._slot = None

    def clear_expired(self, now: datetime) -> int:
        return 0


class FakeBootstrap:
    def __init__(
        self,
        refresh_result: BootstrapResult | None,
        *,
        refresh_error: Exception | None = None,
    ) -> None:
        self.refresh_result = refresh_result
        self.refresh_error = refresh_error
        self.refresh_calls: list[ActiveCredential] = []

    def refresh(self, current: ActiveCredential) -> BootstrapResult | None:
        self.refresh_calls.append(current)
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.refresh_result

    def validate(self, _material: bytes) -> Principal:
        return Principal(employee_principal="1000000031696069")


def _material(token: str = "token-old") -> bytes:
    return json.dumps(
        {
            "puzu_lease_token": token,
            "puzu_lease_token_secure": token,
            "UCID": "1000000031696069",
            "UCID_secure": "1000000031696069",
            "csrfSecret": "csrf",
            "saas_token": "saas",
            "lianjia_ssid": "ssid-1",
        }
    ).encode("utf-8")


def _credential(*, expires_at: datetime) -> ActiveCredential:
    return ActiveCredential(
        session_id="ssid-1",
        employee_principal="1000000031696069",
        credential_material=_material(),
        expires_at=expires_at,
        credential_version=1,
        refresh_material=b'{"TGC": "TGT-fake"}',
    )


def _fresh_result() -> BootstrapResult:
    return BootstrapResult(
        credential_material=_material("token-fresh"),
        expires_at=NOW + timedelta(hours=1),
        credential_version=1,
        refresh_material=b'{"TGC": "TGT-fake"}',
    )


def _provider(store: FakeStore, bootstrap: FakeBootstrap, handler: Any) -> KecomSessionProvider:
    return KecomSessionProvider(
        Settings(),
        store,
        bootstrap,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(2.0),
            follow_redirects=False,
        ),
        clock=lambda: NOW,
    )


def _ok_envelope() -> dict[str, object]:
    return {"code": 100000, "msg": "加载成功", "data": {}}


# -- local expiry estimate lapsed: silent TGC renewal first ----------------


def test_status_expired_refreshes_via_tgc_and_reports_ready() -> None:
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(_fresh_result())
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    status = provider.status()

    assert status.state is ConnectionState.READY
    assert len(bootstrap.refresh_calls) == 1
    assert store.saved[-1].session_id != "ssid-1"
    assert ("ssid-1", "replaced") in store.invalidated


def test_status_expired_refresh_failure_deactivates() -> None:
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    status = provider.status()

    assert status.state is ConnectionState.AUTH_REQUIRED
    assert "rescan" in status.message
    assert store.load_active() is None
    assert ("ssid-1", "expired") in store.invalidated


def test_authorized_fetch_expired_refreshes_before_sending() -> None:
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(_fresh_result())
    seen_cookie: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/houseList/search/pc/list"):
            seen_cookie.append(request.headers.get("cookie", ""))
            return httpx.Response(
                200,
                json={"code": 100000, "msg": "加载成功", "data": {"list": [], "totalCount": 0}},
            )
        return httpx.Response(500)

    provider = _provider(store, bootstrap, handler)

    response = provider.authorized_fetch(
        AuthorizedRequest(
            route="rental_listing.search",
            method="GET",
            query={"pageIndex": 1, "pageSize": 10},
            body=None,
            request_id="req-1",
        )
    )

    assert response.status_code == 200
    assert "token-fresh" in seen_cookie[0]
    assert len(bootstrap.refresh_calls) == 1


def test_authorized_fetch_expired_refresh_failure_raises_auth_required() -> None:
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    from app.domain.errors import AuthenticationRequiredError

    try:
        provider.authorized_fetch(
            AuthorizedRequest(
                route="rental_listing.search",
                method="GET",
                query={},
                body=None,
                request_id="req-1",
            )
        )
    except AuthenticationRequiredError:
        pass
    else:
        raise AssertionError("expired + failed refresh must raise AuthenticationRequiredError")
    assert store.load_active() is None


# -- keepalive probe success rolls the local estimate forward --------------


def test_keepalive_success_rolls_expiry_forward() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=10)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(
        store, bootstrap, handler=lambda req: httpx.Response(200, json=_ok_envelope())
    )

    status = provider.run_keepalive()

    assert status.state is ConnectionState.READY
    assert status.expires_at == NOW + timedelta(hours=1)
    assert len(store.saved) == 1
    assert store.saved[0].expires_at == NOW + timedelta(hours=1)
    assert bootstrap.refresh_calls == []


def test_keepalive_success_keeps_distant_deadline_untouched() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(
        store, bootstrap, handler=lambda req: httpx.Response(200, json=_ok_envelope())
    )

    status = provider.run_keepalive()

    assert status.state is ConnectionState.READY
    assert status.expires_at == NOW + timedelta(minutes=50)
    assert store.saved == []  # no needless store rewrite


def test_keepalive_probe_rejection_refreshes_via_tgc() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(_fresh_result())
    provider = _provider(
        store,
        bootstrap,
        handler=lambda req: httpx.Response(200, json={"code": 403, "msg": "用户未登录"}),
    )

    status = provider.run_keepalive()

    assert status.state is ConnectionState.READY
    assert len(bootstrap.refresh_calls) == 1
    assert store.saved[-1].session_id != "ssid-1"
    assert ("ssid-1", "replaced") in store.invalidated


def test_successful_keepalive_clears_previous_degraded_latch() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(
        store,
        bootstrap,
        handler=lambda req: httpx.Response(200, json=_ok_envelope()),
    )
    provider._degraded_message = "old credential failed"  # type: ignore[attr-defined]

    status = provider.run_keepalive()

    assert status.state is ConnectionState.READY
    assert provider.status().state is ConnectionState.READY


def test_fresh_credential_install_clears_previous_degraded_latch() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))
    provider._degraded_message = "old credential failed"  # type: ignore[attr-defined]

    provider.install_fresh_credential(_fresh_result())

    assert provider.status().state is ConnectionState.READY


def test_keepalive_refresh_exception_deactivates_for_qr_login() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None, refresh_error=RuntimeError("bootstrap failed"))
    provider = _provider(
        store,
        bootstrap,
        handler=lambda req: httpx.Response(200, json={"code": 403, "msg": "用户未登录"}),
    )

    status = provider.run_keepalive()

    assert status.state is ConnectionState.AUTH_REQUIRED
    assert store.load_active() is None
