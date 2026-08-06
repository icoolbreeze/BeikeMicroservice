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
    refresh_keepalive_interval_seconds=60,
)


def _settings(**overrides: Any) -> Settings:
    merged = {**_SETTINGS_DEFAULTS, **overrides}
    return Settings(**merged)


class _Recorder:
    """Captures calls to a fake renderer so tests can assert bootstrap output."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def render(self, payload: str, *, note: str) -> None:
        self.calls.append((payload, note))


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
    provider._exchange_ticket = lambda ticket, **kw: (  # type: ignore[method-assign]
        {"puzu_lease_token": "tok", "UCID": "1000000031696069", "csrfSecret": "s", "lianjia_ssid": "ssid-1"},
        {"TGC": "t", "security_ticket": "st", "login_ucid": "1000000031696069", "lianjia_ssid": "ssid-1"},
    )

    result = provider.bootstrap()
    assert recorder.calls and recorder.calls[0][0] == "https://t.lianjia.com/XXXXXX"
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

    from app.infrastructure.kecom_qr_bootstrap import _BootstrapExpired
    with pytest.raises(_BootstrapExpired):
        provider.bootstrap()
    provider.close()


def test_expired_qrcode_raises_bootstrap_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authentication/initialize":
            return httpx.Response(200, json=_initialize_response())
        if request.url.path == "/authentication/qrcode/query":
            return httpx.Response(200, json={"state": "EXPIRED", "success": True})
        return httpx.Response(404)

    settings = _settings()
    recorder = _Recorder()
    fixed_clock = iter([datetime(2026, 8, 6, 0, 0, tzinfo=UTC) + timedelta(seconds=i) for i in range(50)])

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = KeComQrBootstrapProvider(
        settings,
        client=client,
        qr_renderer=recorder,
        clock=lambda: next(fixed_clock),
        sleep=lambda s: None,
    )

    from app.infrastructure.kecom_qr_bootstrap import _BootstrapExpired
    with pytest.raises(_BootstrapExpired):
        provider.bootstrap()
    provider.close()


def test_refresh_uses_tgc_to_request_new_ticket_then_exchanges() -> None:
    """refresh() must: 1) GET ke.com/login?service=, 2) parse Location,
    3) exchange the new ST ticket for cookies."""

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        # Step 1: ke.com/login?service=...
        if host == "login.ke.com.test" and path == "/login":
            captured["ke_service"] = request.url.params.get("service")
            captured["ke_cookie"] = request.headers.get("cookie", "")
            return httpx.Response(
                302,
                headers={"location": "https://lease.example.test/login?gotoURL=%252F&ticket=ST-NEW-ke.com"},
            )
        # Step 2: lease.example.test/login?...&ticket=ST-NEW-ke.com  (follows redirect)
        if host == "lease.example.test" and path == "/login" and "ticket" in request.url.params:
            captured["lease_ticket"] = request.url.params.get("ticket")
            return httpx.Response(
                302,
                headers=[
                    ("location", "/rent/house/list"),
                    ("set-cookie", "puzu_lease_token=t2; HttpOnly; Path=/"),
                    ("set-cookie", "UCID=1000000031696069; HttpOnly; Path=/"),
                    ("set-cookie", "csrfSecret=s2; HttpOnly; Path=/"),
                    ("set-cookie", "lianjia_ssid=ssid-2; Path=/"),
                ],
            )
        # Final landing which we expect follow_redirects to chase.
        if host == "lease.example.test" and path == "/rent/house/list":
            return httpx.Response(200, text="<html>ok</html>")
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
    assert captured["ke_service"] == "https://lease.example.test/login?gotoURL=%252F"
    assert "TGC=tgt-1" in captured["ke_cookie"]
    assert captured["lease_ticket"] == "ST-NEW-ke.com"
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
