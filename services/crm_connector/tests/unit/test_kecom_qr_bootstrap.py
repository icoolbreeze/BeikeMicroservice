from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.infrastructure.kecom_qr_bootstrap import KeComQrBootstrapProvider
from app.infrastructure.settings import Settings


_SETTINGS_DEFAULTS = dict(
    bound_employee_principal="",
    mcp_transport="stdio",
    upstream_profile="unconfigured",
    request_timeout_seconds=2.0,
    cors_origins=(),
    crm_login_base="https://login.ke.com.test",
    crm_service_url="https://lease.example.test/login?gotoURL=%252F",
    crm_business_origin="https://lease.example.test",
    crm_default_city_code="510100",
    credential_store_path="./run/fixture.bin",
    authd_listen_address="127.0.0.1:8021",
    bootstrap_poll_interval_seconds=0.0,
    bootstrap_poll_timeout_seconds=10.0,
    bootstrap_qrcode_refresh_initial_delay_seconds=0.0,
    refresh_keepalive_interval_seconds=60,
)


def _settings(**overrides: Any) -> Settings:
    merged = {**_SETTINGS_DEFAULTS, **overrides}
    return Settings(**merged)


class _Recorder:
    """Captures calls to a fake renderer so tests can assert bootstrap output."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.close_calls = 0
        self.pump_calls = 0

    def render(self, payload: str, *, note: str) -> None:
        self.calls.append((payload, note))

    def pump(self) -> None:
        self.pump_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def _build_client(handler: httpx.MockTransport | None = None) -> httpx.Client:
    return httpx.Client(transport=handler or httpx.MockTransport(lambda req: httpx.Response(404)))


def _initialize_response(qrcode_id: str = "qr-1", qr_content: str = "https://t.lianjia.com/XXXXXX") -> dict[str, Any]:
    return {
        "success": True,
        "loginTicketId": "ticket-1",
        "authenticationMethods": {
            "employee": [
                {"type": "oauth-employee", "allianceMethods": [], "initialOptions": {}},
                {
                    "type": "qrcode",
                    "allianceMethods": [],
                    "initialOptions": {"id": qrcode_id, "qrCodeContent": qr_content},
                },
            ],
            "customer": [],
        },
        "publicKey": {"appEncrypt": "true", "version": "1", "key": "<unused>"},
        "supportedAccountSystems": [
            {"id": "customer", "name": "用户", "viewStyle": {"register-entry": "/register"}},
            {"id": "employee", "name": "员工", "viewStyle": {}},
        ],
    }


def test_initialize_extracts_qrcode_and_renders_payload() -> None:
    boot_calls: list[httpx.Request] = []
    poll_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authentication/initialize":
            boot_calls.append(request)
            return httpx.Response(200, json=_initialize_response())
        if request.url.path == "/authentication/qrcode/query":
            poll_calls.append(request)
            state = {"CREATED": 0, "BINDING": 1, "CONFIRMED": 2}
            n = len(poll_calls)
            current = next(s for s, c in state.items() if c == (0 if n < 1 else 1) and n <= 2) if n < 3 else "CONFIRMED"
            return httpx.Response(200, json={"state": current if n < 3 else "CONFIRMED", "success": True, "ticket": "ST-1-X-ke.com"})
        return httpx.Response(404)

    settings = _settings()
    recorder = _Recorder()
    times = [datetime(2026, 8, 6, 0, 0, tzinfo=UTC) + timedelta(seconds=i) for i in range(50)]
    clock_iter = iter(times)
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=recorder,
        clock=lambda: next(clock_iter),
        sleep=fake_sleep,
    )

    # Patch _exchange_ticket to return a known cookie set so we avoid the
    # second leg of HTTP. This isolates bootstrap() to the polling protocol.
    provider._exchange_ticket = lambda ticket, qrcode_id="", login_ticket_id="", **kw: (  # type: ignore[method-assign]
        {"puzu_lease_token": "tok", "UCID": "1000000031696069", "csrfSecret": "s", "lianjia_ssid": "ssid-1"},
        {"TGC": "t", "security_ticket": "st", "login_ucid": "1000000031696069", "lianjia_ssid": "ssid-1"},
    )

    result = provider.bootstrap()
    assert recorder.calls and recorder.calls[0][0] == "https://t.lianjia.com/XXXXXX"
    assert recorder.close_calls == 1  # confirmed scan closes the local QR window
    material = json.loads(result.credential_material.decode("utf-8"))
    assert material["puzu_lease_token"] == "tok"
    assert material["UCID"] == "1000000031696069"
    assert json.loads(result.refresh_material.decode("utf-8"))["TGC"] == "t"
    provider.close()


def test_polling_times_out_and_returns_no_cookies() -> None:
    poll_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_calls
        if request.url.path == "/authentication/initialize":
            return httpx.Response(200, json=_initialize_response())
        if request.url.path == "/authentication/qrcode/query":
            poll_calls += 1
            return httpx.Response(200, json={"state": "CREATED", "success": True})
        return httpx.Response(404)

    settings = _settings(bootstrap_poll_timeout_seconds=1.0)
    recorder = _Recorder()

    counter = {"n": 0}

    def fake_clock() -> datetime:
        counter["n"] += 1
        return datetime(2026, 8, 6, 0, 0, tzinfo=UTC) + timedelta(seconds=counter["n"])

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=recorder,
        clock=fake_clock,
        sleep=lambda s: None,
    )

    from app.infrastructure.kecom_qr_bootstrap import _BootstrapPollTimedOut
    with pytest.raises(_BootstrapPollTimedOut):
        provider.bootstrap()
    provider.close()


def test_expired_qrcode_automatically_refreshes_then_confirms() -> None:
    initialized = 0
    rendered = _Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal initialized
        if request.url.path == "/authentication/initialize":
            initialized += 1
            return httpx.Response(
                200,
                json=_initialize_response(
                    qrcode_id=f"qr-{initialized}",
                    qr_content=f"https://t.lianjia.com/QR{initialized}",
                ),
            )
        if request.url.path == "/authentication/qrcode/query":
            if request.url.params["id"] == "qr-1":
                return httpx.Response(200, json={"state": "EXPIRED", "success": True})
            return httpx.Response(
                200,
                json={"state": "CONFIRMED", "success": True, "ticket": "ST-1-X-ke.com"},
            )
        return httpx.Response(404)

    settings = _settings()
    fixed_clock = iter([datetime(2026, 8, 6, 0, 0, tzinfo=UTC) + timedelta(seconds=i) for i in range(50)])

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=rendered,
        clock=lambda: next(fixed_clock),
        sleep=lambda s: None,
    )
    provider._exchange_ticket = lambda ticket, qrcode_id="", login_ticket_id="", **kw: (  # type: ignore[method-assign]
        {"puzu_lease_token": "tok", "UCID": "1000000031696069", "csrfSecret": "s"},
        {"TGC": "t"},
    )

    result = provider.bootstrap()
    assert initialized == 2
    assert [payload for payload, _ in rendered.calls] == [
        "https://t.lianjia.com/QR1",
        "https://t.lianjia.com/QR2",
    ]
    assert json.loads(result.credential_material.decode("utf-8"))["UCID"] == "1000000031696069"
    provider.close()


def test_authenticate_exchange_on_ticketless_confirm() -> None:
    """When the CONFIRMED payload carries no ticket, bootstrap() must replay
    the desktop passport-web flow: POST /authentication/authenticate (sets
    TGC) -> GET login.ke.com/login?service=<lease-pz's own encoded login url>
    -> CAS validates TGC, mints ST -> 302 to lease-pz/login?gotoURL=...&ticket=ST
    -> lease-pz validates -> plants puzu_lease_token / UCID / csrfSecret."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "login.ke.com.test" and path == "/authentication/initialize":
            return httpx.Response(200, json=_initialize_response())
        if host == "login.ke.com.test" and path == "/authentication/qrcode/query":
            captured["polls"] = captured.get("polls", 0) + 1
            if captured["polls"] == 1:
                return httpx.Response(200, json={"state": "CREATED", "success": True})
            return httpx.Response(200, json={"state": "CONFIRMED", "success": True})
        if host == "login.ke.com.test" and path == "/authentication/authenticate":
            captured["auth_body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"status": "PASS"},
                headers=[("set-cookie", "TGC=tgt-auth; HttpOnly; Path=/")],
            )
        if host == "login.ke.com.test" and path == "/login":
            service = request.url.params.get("service", "")
            if service.startswith("https://lease.example.test"):
                captured["cas_cookie"] = request.headers.get("cookie", "")
                return httpx.Response(
                    302,
                    headers={"location": "https://lease.example.test/login?gotoURL=%25252Frent%25252Fhouse%25252Flist%25253FisSaaS%25253Dfalse&ticket=ST-SSO-auth-ke.com"},
                )
            if service.startswith("https://house.link.lianjia.com"):
                return httpx.Response(
                    302,
                    headers={"location": "https://house.link.lianjia.com/shiro-cas?ticket=ST-SHIRO-auth-ke.com"},
                )
            return httpx.Response(404)
        if host in ("house.example.test", "house.link.lianjia.com") and path == "/shiro-cas":
            return httpx.Response(
                302,
                headers=[
                    ("location", "/search/sale/default/gdiv_mt"),
                    ("set-cookie", "saas_token=stok; HttpOnly; Path=/"),
                    ("set-cookie", "HOUSEJSESSIONID=hjsess; HttpOnly; Path=/"),
                ],
            )
        if host in ("house.example.test", "house.link.lianjia.com") and path == "/search/sale/default/gdiv_mt":
            return httpx.Response(200, text="<html>house</html>")
        if host == "lease.example.test" and path == "/rent/house/list":
            captured["final_landed"] = True
            return httpx.Response(200, text="<html>ok</html>")
        if host == "lease.example.test" and path == "/login" and "ticket" in request.url.params:
            captured["lease_ticket"] = request.url.params.get("ticket")
            return httpx.Response(
                302,
                headers=[
                    ("location", "/rent/house/list?isSaaS=false"),
                    ("set-cookie", "puzu_lease_token=pauth; HttpOnly; Path=/"),
                    ("set-cookie", "UCID=1000000031696069; HttpOnly; Path=/"),
                    ("set-cookie", "csrfSecret=sauth; HttpOnly; Path=/"),
                    ("set-cookie", "lianjia_ssid=ssid-auth; Path=/"),
                ],
            )
        return httpx.Response(404)

    settings = _settings()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=_Recorder(),
        clock=lambda: datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
        sleep=lambda s: None,
    )

    result = provider.bootstrap()
    assert captured["auth_body"]["service"] == "https://lease.example.test/login?gotoURL=%252F"
    assert captured["auth_body"]["mainAuthMethodName"] == "qrcode"
    assert captured["auth_body"]["accountSystem"] == "employee"
    assert captured["auth_body"]["loginTicketId"] == "ticket-1"
    assert captured["auth_body"]["context"] == {"deviceId": "default", "sign": "default"}
    assert captured["auth_body"]["credential"]["id"] == "qr-1"
    assert "TGC=tgt-auth" in captured["cas_cookie"]
    assert captured["lease_ticket"] == "ST-SSO-auth-ke.com"
    assert captured["final_landed"] is True
    business = json.loads(result.credential_material.decode("utf-8"))
    assert business["puzu_lease_token"] == "pauth"
    assert business["saas_token"] == "stok"
    assert business["UCID"] == "1000000031696069"
    refresh = json.loads(result.refresh_material.decode("utf-8"))
    assert refresh["TGC"] == "tgt-auth"
    provider.close()


def test_qrcode_refresh_backoff_is_bounded() -> None:
    provider = KeComQrBootstrapProvider(
        _settings(bootstrap_qrcode_refresh_initial_delay_seconds=2.0),
        client=_build_client(),
    )

    assert provider._qrcode_refresh_delay(1) == 2.0
    assert provider._qrcode_refresh_delay(2) == 4.0
    assert provider._qrcode_refresh_delay(9) == 30.0
    provider.close()


def test_refresh_uses_tgc_to_walk_cas_chain() -> None:
    """refresh() must mint an ST directly via CAS front channel: GET
    login.ke.com/login?service=<lease's own encoded url> (TGC in jar) ->
    302 to lease-pz/login?...&ticket=ST -> lease-pz validates and plants
    puzu_lease_token / UCID / csrfSecret -> 302 to list page 200."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "lease.example.test" and path == "/rent/house/list":
            captured["final_landed"] = True
            return httpx.Response(200, text="<html>ok</html>")
        if host == "login.ke.com.test" and path == "/login":
            service = request.url.params.get("service", "")
            if service.startswith("https://lease.example.test"):
                captured["cas_cookie"] = request.headers.get("cookie", "")
                return httpx.Response(
                    302,
                    headers={"location": "https://lease.example.test/login?gotoURL=%25252Frent%25252Fhouse%25252Flist%25253FisSaaS%25253Dfalse&ticket=ST-NEW-ke.com"},
                )
            if service.startswith("https://house.link.lianjia.com"):
                return httpx.Response(
                    302,
                    headers={"location": "https://house.link.lianjia.com/shiro-cas?ticket=ST-SHIRO-new-ke.com"},
                )
            return httpx.Response(404)
        if host in ("house.example.test", "house.link.lianjia.com") and path == "/shiro-cas":
            return httpx.Response(
                302,
                headers=[
                    ("location", "/search/sale/default/gdiv_mt"),
                    ("set-cookie", "saas_token=stok2; HttpOnly; Path=/"),
                ],
            )
        if host in ("house.example.test", "house.link.lianjia.com") and path == "/search/sale/default/gdiv_mt":
            return httpx.Response(200, text="<html>house</html>")
        if host == "lease.example.test" and path == "/login" and "ticket" in request.url.params:
            captured["lease_ticket"] = request.url.params.get("ticket")
            return httpx.Response(
                302,
                headers=[
                    ("location", "/rent/house/list?isSaaS=false"),
                    ("set-cookie", "puzu_lease_token=t2; HttpOnly; Path=/"),
                    ("set-cookie", "UCID=1000000031696069; HttpOnly; Path=/"),
                    ("set-cookie", "csrfSecret=s2; HttpOnly; Path=/"),
                    ("set-cookie", "lianjia_ssid=ssid-2; Path=/"),
                ],
            )
        return httpx.Response(404)

    settings = _settings()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=_Recorder(),
        clock=lambda: datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
        sleep=lambda s: None,
    )

    from app.domain.providers.credential_store import ActiveCredential

    current = ActiveCredential(
        session_id="old-sid",
        employee_principal="1000000031696069",
        credential_material=b'{"puzu_lease_token":"old","UCID":"1000000031696069","csrfSecret":"old"}',
        expires_at=datetime(2026, 8, 6, 1, 0, tzinfo=UTC),
        credential_version=1,
        refresh_material=b'{"TGC":"tgt-1","security_ticket":"st","login_ucid":"1000000031696069","lianjia_ssid":"ssid-old"}',
    )
    result = provider.refresh(current)
    assert result is not None
    assert "TGC=tgt-1" in captured["cas_cookie"]
    assert captured["lease_ticket"] == "ST-NEW-ke.com"
    assert captured["final_landed"] is True
    new_business = json.loads(result.credential_material.decode("utf-8"))
    assert new_business["puzu_lease_token"] == "t2"
    assert new_business["UCID"] == "1000000031696069"
    provider.close()


def test_refresh_returns_none_when_tgc_missing() -> None:
    settings = _settings()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=_Recorder(),
        clock=lambda: datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
        sleep=lambda s: None,
    )
    from app.domain.providers.credential_store import ActiveCredential

    current = ActiveCredential(
        session_id="sid",
        employee_principal="1000000031696069",
        credential_material=b"{}",
        expires_at=None,
        credential_version=1,
        refresh_material=None,
    )
    assert provider.refresh(current) is None
    provider.close()


def test_validate_returns_principal_from_account_right_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/accountRightInfo")
        assert "typeList" in request.url.params
        return httpx.Response(200, json={"code": 100000, "msg": "ok", "data": {}})

    settings = _settings()
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=_Recorder(),
        clock=lambda: datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
        sleep=lambda s: None,
    )
    material = json.dumps({
        "puzu_lease_token": "t",
        "UCID": "1000000031696069",
        "UCID_secure": "1000000031696069",
        "csrfSecret": "s",
        "Lianjia_u_info": "%7B%22name%22%3A%22%E5%BC%A0%E4%B8%89%22%7D",
    }).encode("utf-8")

    principal = provider.validate(material)
    assert principal.employee_principal == "1000000031696069"
    assert principal.display_name == "张三"
    provider.close()
