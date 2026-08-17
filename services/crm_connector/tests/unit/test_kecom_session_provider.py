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


def test_status_expired_is_local_only_and_reports_expiring() -> None:
    # Lazy validation: status() must not touch the upstream (no TGC walk,
    # no probe) when the local estimate lapses; the renewal belongs to the
    # next authorized_fetch, so idle status polling stays request-free.
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(_fresh_result())
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    status = provider.status()

    assert status.state is ConnectionState.EXPIRING
    assert bootstrap.refresh_calls == []
    assert store.load_active() is not None
    assert store.saved == []


def test_status_expired_refresh_failure_does_not_deactivate() -> None:
    # Even when the TGC material is dead, status() stays a local read:
    # deactivation is decided by the request path, so a status poll at
    # idle must never kill a credential the next call could recover.
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    status = provider.status()

    assert status.state is ConnectionState.EXPIRING
    assert bootstrap.refresh_calls == []
    assert store.load_active() is not None
    assert store.invalidated == []


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


def test_authorized_fetch_success_rolls_expiry_forward() -> None:
    # Lazy keepalive: a successful business response replaces the periodic
    # probe — it proves the session is alive upstream and rolls the local
    # estimate forward without a dedicated keepalive call.
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=10)))
    bootstrap = FakeBootstrap(None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/houseList/search/pc/list")
        return httpx.Response(
            200,
            json={"code": 100000, "msg": "加载成功", "data": {"list": [], "totalCount": 0}},
        )

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
    assert bootstrap.refresh_calls == []
    assert store.saved[-1].expires_at == NOW + timedelta(hours=1)


def test_default_client_headers_mirror_workbench_browser() -> None:
    # Outbound headers must match the workbench browser signature instead
    # of announcing an automation client to the upstream risk control.
    settings = Settings()
    provider = KecomSessionProvider(
        settings, FakeStore(None), FakeBootstrap(None), clock=lambda: NOW
    )
    try:
        assert (
            provider._client.headers["user-agent"]  # type: ignore[attr-defined]
            == settings.http_user_agent
        )
        assert "Mozilla/5.0" in settings.http_user_agent
        assert "Chrome/" in settings.http_user_agent
        assert (
            provider._client.headers["accept-language"]  # type: ignore[attr-defined]
            == settings.http_accept_language
        )
    finally:
        provider.close()


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


def _fresh_result_with_ssid(ssid: str) -> BootstrapResult:
    return BootstrapResult(
        credential_material=json.dumps(
            {
                "puzu_lease_token": "token-fresh",
                "puzu_lease_token_secure": "token-fresh",
                "UCID": "1000000031696069",
                "UCID_secure": "1000000031696069",
                "csrfSecret": "csrf",
                "saas_token": "saas",
                "lianjia_ssid": ssid,
            }
        ).encode("utf-8"),
        expires_at=NOW + timedelta(hours=1),
        credential_version=1,
        refresh_material=b'{"TGC": "TGT-fake"}',
    )


def test_stale_session_deactivation_does_not_poison_fresh_credential() -> None:
    """A rejection of a replaced session id must not latch the process
    into the degraded 'upstream rejected' mode while a valid credential
    is active (the race that locked the stdio MCP process until restart)."""
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    provider.install_fresh_credential(_fresh_result_with_ssid("ssid-fresh"))

    # Late response of a request sent with the old credential is rejected.
    provider._deactivate("ssid-1", "upstream_rejected")

    status = provider.status()
    assert status.state is ConnectionState.READY
    active = store.load_active()
    assert active is not None and active.session_id == "ssid-fresh"


def test_current_session_deactivation_still_sets_degraded_latch() -> None:
    """Deactivating the credential that is actually active keeps the
    original semantics: slot cleared and the degraded latch raised."""
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(None)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(500))

    provider._deactivate("ssid-1", "upstream_rejected")

    assert store.load_active() is None
    assert provider.status().state is ConnectionState.AUTH_REQUIRED


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


# -- principal survives silent TGC renewal (whoami regression) ---------------


def test_refresh_preserves_employee_principal_from_material() -> None:
    """The renewed business session re-plants UCID; the saved credential must
    carry it so crm_whoami / bound-principal checks keep working."""
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(_fresh_result())
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(200, json=_ok_envelope()))

    response = provider.authorized_fetch(
        AuthorizedRequest(
            route="rental_listing.search",
            method="GET",
            query={},
            body=None,
            request_id="req-1",
        )
    )

    assert response.status_code == 200
    saved = store.saved[-1]
    assert saved.employee_principal == "1000000031696069"


def test_refresh_keeps_previous_principal_without_material_ucid() -> None:
    """Material without a UCID cookie falls back to the replaced credential's
    principal instead of hardcoding the empty string."""
    result = BootstrapResult(
        credential_material=json.dumps({"puzu_lease_token": "t"}).encode("utf-8"),
        expires_at=NOW + timedelta(hours=1),
        credential_version=1,
        refresh_material=b'{"TGC": "TGT-fake"}',
    )
    store = FakeStore(_credential(expires_at=NOW - timedelta(minutes=1)))
    bootstrap = FakeBootstrap(result)
    provider = _provider(store, bootstrap, handler=lambda req: httpx.Response(200, json=_ok_envelope()))

    provider.authorized_fetch(
        AuthorizedRequest(
            route="rental_listing.search",
            method="GET",
            query={},
            body=None,
            request_id="req-1",
        )
    )

    assert store.saved[-1].employee_principal == "1000000031696069"


def test_bound_principal_falls_back_to_material_ucid() -> None:
    """Credentials installed by older renewals recorded an empty principal;
    the UCID cookie inside the material is the same identity."""
    stale = ActiveCredential(
        session_id="ssid-1",
        employee_principal="",
        credential_material=_material(),
        expires_at=NOW + timedelta(minutes=50),
        credential_version=1,
        refresh_material=b'{"TGC": "TGT-fake"}',
    )
    store = FakeStore(stale)
    provider = _provider(store, FakeBootstrap(None), handler=lambda req: httpx.Response(500))

    principal = provider.bound_principal()

    assert principal is not None
    assert principal.employee_principal == "1000000031696069"


# -- 200 + non-JSON body surfaces as contract drift --------------------------


def test_authorized_fetch_non_json_body_raises_upstream_changed() -> None:
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    provider = _provider(
        store,
        FakeBootstrap(None),
        handler=lambda req: httpx.Response(200, text="<html>login page</html>"),
    )

    from app.domain.errors import UpstreamChangedError

    try:
        provider.authorized_fetch(
            AuthorizedRequest(request_id="t", route="identity.me", method="GET", query={}, body=None)
        )
    except UpstreamChangedError as exc:
        assert "non-JSON" in str(exc)
    else:
        raise AssertionError("expected UpstreamChangedError")


# -- autorefresh no longer invalidates the old credential upfront ------------


def test_autorefresh_success_invalidates_old_as_replaced_not_rejected() -> None:
    """A 401 followed by a successful renewal must leave the old credential
    invalidated as ``replaced``; the pre-refresh deactivation used to mark it
    ``upstream_rejected`` (and discarded it when the refresh hit a network
    error)."""
    store = FakeStore(_credential(expires_at=NOW + timedelta(minutes=50)))
    bootstrap = FakeBootstrap(_fresh_result())
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"code": 403, "msg": "用户未登录"})
        return httpx.Response(200, json=_ok_envelope())

    provider = _provider(store, bootstrap, handler=handler)

    provider.authorized_fetch(
        AuthorizedRequest(request_id="t", route="identity.me", method="GET", query={}, body=None)
    )

    rejected = [reason for sid, reason in store.invalidated if sid == "ssid-1" and reason == "upstream_rejected"]
    replaced = [reason for sid, reason in store.invalidated if sid == "ssid-1" and reason == "replaced"]
    assert not rejected
    assert replaced
