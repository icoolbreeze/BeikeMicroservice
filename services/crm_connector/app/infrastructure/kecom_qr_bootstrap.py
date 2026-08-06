from __future__ import annotations

import io
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import httpx

from app.domain.models import Principal
from app.domain.providers.credential_bootstrap_provider import (
    BootstrapResult,
)
from app.domain.providers.credential_store import ActiveCredential
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

# Injectable clock and sleep used by the bootstrap flow. Tests swap these in
# to fast-forward polling without waiting real seconds.
Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


Sleep = Callable[[float], None]


def _real_sleep(seconds: float) -> None:
    time.sleep(seconds)


# Cookie domains we harvest from each step of the auth flow. Real upstream
# addresses come from Settings; the value list here is used only to filter the
# client jar and is safe to hardcode because the domains belong to the CRM
# vendor, not to any deployment secret.
_BUSINESS_COOKIE_NAMES = (
    "puzu_lease_token",
    "puzu_lease_token_secure",
    "UCID",
    "UCID_secure",
    "csrfSecret",
    "Lianjia_curWorkCity",
    "Lianjia_BUcid",
    "Lianjia_u_info",
    "lianjia_ssid",
    "lianjia_uuid",
    "saas_token",
    "login_ucid",
)

_REFRESH_COOKIE_NAMES = (
    "TGC",
    "TGC_Secure",
    "security_ticket",
    "login_ucid",
    "lianjia_ssid",
    "lianjia_uuid",
)

_QRCODE_PATH = "/authentication/initialize"
_QRCODE_QUERY_PATH = "/authentication/qrcode/query"
_LOGOUT_PATH = "/logout"
_EMPLOYEE_ACCOUNT_SYSTEM = "employee"
_QRCODE_METHOD_TYPE = "qrcode"

# Ke.com issues one-time service tickets that look like ``ST-<digits>-<base62>-ke.com``.
# We only use this pattern for parsing the Location header on the CONFIRMED
# redirect, never to log or persist a value.
_TICKET_RE = re.compile(r"ticket=(ST-[^&]+)")


@dataclass(frozen=True)
class _QrCodeHandle:
    login_ticket_id: str
    qrcode_id: str
    qrcode_content: str


@dataclass(frozen=True)
class _ConfirmedResult:
    service_ticket: str
    business_cookies: dict[str, str]
    refresh_cookies: dict[str, str]


class KeComQrBootstrapProvider:
    """Drive the login.ke.com qrcode SSO flow over httpx.

    Implements ``CredentialBootstrapProvider`` so that ``crm-authd`` can perform
    a one-shot human-assisted bootstrap (``bootstrap``) plus automated refreshes
    afterwards (``refresh``), without involving a browser. The provider never
    discloses raw credential material to callers; the bytes returned in
    ``BootstrapResult`` are an opaque, deployment-sized cookie jar.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        qr_renderer: "_QrRenderer | None" = None,
        clock: Clock = system_clock,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._settings = settings
        self._client_owner = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            headers={"user-agent": "crm-connector/0.1"},
        )
        self._qr = qr_renderer or _TerminalQrRenderer()
        self._clock = clock
        self._sleep = sleep

    # -- CredentialBootstrapProvider ----------------------------------------

    def bootstrap(self) -> BootstrapResult:
        handle = self._initialize()
        self._qr.render(
            handle.qrcode_content,
            note=(
                "请使用 Link/A+/D+/Studio/企微 扫描上方二维码登录。\n"
                "Connector 轮询手机端确认状态……"
            ),
        )
        confirmed = self._poll_until_confirmed(handle.qrcode_id)
        business_cookies, refresh_cookies = self._exchange_ticket(confirmed.service_ticket)
        return self._build_result(business_cookies, refresh_cookies)

    def refresh(self, current: ActiveCredential) -> BootstrapResult | None:
        refresh_cookies = self._deserialize_refresh_material(current.refresh_material)
        if not refresh_cookies or "TGC" not in refresh_cookies:
            return None

        # Step 0 — the refresh cookies live on the ke.com domain; seed them into
        # the client's domain-scoped jar so the CAS request carries them without
        # leaking them onto the business domain later.
        self._seed_cookies(self._settings.crm_login_base, refresh_cookies)

        # Step 1 — ask the SSO server for a fresh ST ticket using the existing TGC.
        new_login_response = self._client.get(
            f"{self._settings.crm_login_base}/login",
            params={"service": self._settings.crm_service_url},
            follow_redirects=False,
        )
        if new_login_response.status_code != 302:
            logger.info("auth.refresh.rejected status=%s", new_login_response.status_code)
            return None
        location = new_login_response.headers.get("location", "")
        match = _TICKET_RE.search(location)
        if not match:
            logger.info("auth.refresh.no_ticket location=%r", location)
            return None
        service_ticket = match.group(1)

        # Step 2 — exchange the ticket at the business domain to get fresh
        # business cookies. The refresh_cookies are merged in to keep the SSO
        # linkage stable; the response Set-Cookie updates the jar.
        business_cookies, refreshed_refresh_cookies = self._exchange_ticket(service_ticket)
        return self._build_result(business_cookies, refreshed_refresh_cookies)

    def validate(self, credential_material: bytes) -> Principal:
        cookies = self._deserialize_business_material(credential_material)
        if not cookies or "UCID" not in cookies:
            raise _BootstrapError("credential_material missing required UCID cookie")

        self._seed_cookies(self._settings.crm_business_origin, cookies)

        # accountRightInfo returns the authenticated principal plus account
        # rights. We only read UCID and the display name because those are the
        # fields validated by ConnectorService._verify_bound_principal.
        response = self._client.get(
            f"{self._settings.crm_business_origin}/api/puzuHouse/puzu/house/auth/pc/accountRightInfo",
            params={"typeList": "2"},
            headers={
                "x-requested-with": "XMLHttpRequest",
                "house_current_work_citycode": self._settings.crm_default_city_code,
                "referer": f"{self._settings.crm_business_origin}/rent/house/list?isSaaS=false",
            },
        )
        if response.status_code != 200:
            raise _BootstrapError(
                f"accountRightInfo returned status {response.status_code}"
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise _BootstrapError("accountRightInfo response is not JSON") from exc
        if body.get("code") not in (100000, 0):
            raise _BootstrapError(
                f"accountRightInfo rejected material code={body.get('code')}"
            )

        display_name = self._decode_display_name(cookies)
        return Principal(
            employee_principal=cookies["UCID"],
            display_name=display_name,
        )

    def revoke(self, current: ActiveCredential) -> None:
        refresh_cookies = self._deserialize_refresh_material(current.refresh_material)
        if not refresh_cookies:
            return
        try:
            self._seed_cookies(self._settings.crm_login_base, refresh_cookies)
            self._client.post(f"{self._settings.crm_login_base}{_LOGOUT_PATH}")
        except httpx.HTTPError:
            logger.warning("auth.revoke.logout_failed", exc_info=True)

    # -- close -------------------------------------------------------------

    def close(self) -> None:
        if self._client_owner:
            self._client.close()

    # -- internal: bootstrap primitives ------------------------------------

    def _initialize(self) -> _QrCodeHandle:
        response = self._client.post(
            f"{self._settings.crm_login_base}{_QRCODE_PATH}",
            json={
                "service": self._settings.crm_service_url,
                "context": {"deviceId": "default", "sign": "default"},
                "version": "2.0",
            },
            headers={"content-type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise _BootstrapError(f"initialize failed: {payload}")

        handle = self._extract_qrcode_handle(payload)
        if handle is None:
            raise _BootstrapError("initialize did not return the qrcode method")
        return handle

    @staticmethod
    def _extract_qrcode_handle(payload: dict[str, Any]) -> _QrCodeHandle | None:
        methods = payload.get("authenticationMethods", {}).get(_EMPLOYEE_ACCOUNT_SYSTEM, [])
        for method in methods:
            if method.get("type") != _QRCODE_METHOD_TYPE:
                continue
            options = method.get("initialOptions", {}) or {}
            qr_id = options.get("id")
            qr_content = options.get("qrCodeContent")
            if qr_id and qr_content:
                return _QrCodeHandle(
                    login_ticket_id=payload.get("loginTicketId", ""),
                    qrcode_id=qr_id,
                    qrcode_content=qr_content,
                )
        return None

    def _poll_until_confirmed(self, qrcode_id: str) -> _ConfirmedResult:
        # We do not know the exact field name the CONFIRMED response uses to
        # carry the service ticket (the SDK drives a window.location.href
        # jump immediately, which destroys the iframe before our CDP hook can
        # read the body). We poll /qrcode/query until state=CONFIRMED, then
        # synthesise the redirect URL ourselves and follow it to obtain the
        # business-domain cookie jar. This matches observed behaviour across
        # two real scan-and-confirm cycles.
        deadline = self._clock() + timedelta(seconds=self._settings.bootstrap_poll_timeout_seconds)
        last_state = "CREATED"
        while self._clock() < deadline:
            response = self._client.get(
                f"{self._settings.crm_login_base}{_QRCODE_QUERY_PATH}",
                params={"id": qrcode_id},
            )
            response.raise_for_status()
            payload = response.json()
            last_state = payload.get("state", "")
            if last_state == "CONFIRMED":
                ticket = self._extract_ticket_from_confirmed(payload)
                if not ticket:
                    raise _BootstrapError("CONFIRMED response missing ticket")
                return _ConfirmedResult(
                    service_ticket=ticket,
                    business_cookies={},
                    refresh_cookies=dict(self._sso_cookies()),
                )
            if last_state == "EXPIRED":
                raise _BootstrapExpired("qrcode expired while polling")
            self._sleep(self._settings.bootstrap_poll_interval_seconds)
        raise _BootstrapExpired(f"polling timed out in state={last_state}")

    @staticmethod
    def _extract_ticket_from_confirmed(payload: dict[str, Any]) -> str | None:
        # Try the most likely field names first; the exact schema is
        # documented in docs/crm-auth-flow-analysis.md §8 as an open question.
        for field in ("ticket", "st", "serviceTicket", "redirectUrl"):
            value = payload.get(field)
            if isinstance(value, str):
                match = _TICKET_RE.search(value)
                if match:
                    return match.group(1)
                if value.startswith("ST-"):
                    return value
        # Some SSO implementations return a redirect URL containing the ticket
        # in the query string.
        redirect = payload.get("redirectUrl") or payload.get("redirect")
        if isinstance(redirect, str):
            match = _TICKET_RE.search(redirect)
            if match:
                return match.group(1)
        return None

    def _exchange_ticket(self, service_ticket: str) -> tuple[dict[str, str], dict[str, str]]:
        # We use the service URL with the ticket appended. The CRM login page
        # at lease-pz.link.lianjia.com validates the ticket server-side and
        # responds with Set-Cookie for the business-domain HttpOnly cookies.
        parsed = urllib.parse.urlsplit(self._settings.crm_service_url)
        login_url = parsed._replace(
            query=(parsed.query + "&" if parsed.query else "") + f"ticket={service_ticket}"
        ).geturl()
        response = self._client.get(
            login_url,
            follow_redirects=True,
        )
        # We do not require 200 here — the CAS-validated Set-Cookie lives in
        # 302 responses too, and httpx.follow_redirects collapses them.
        if response.status_code >= 400:
            raise _BootstrapError(
                f"ticket exchange failed status={response.status_code}"
            )

        all_cookies: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            if cookie.name is not None and cookie.value is not None:
                all_cookies[cookie.name] = cookie.value
        business = {name: all_cookies[name] for name in _BUSINESS_COOKIE_NAMES if name in all_cookies}
        refresh = {name: all_cookies[name] for name in _REFRESH_COOKIE_NAMES if name in all_cookies}
        if not business or "puzu_lease_token" not in business:
            raise _BootstrapError("ticket exchange produced no business cookies")
        return business, refresh

    # -- internal: material helpers ----------------------------------------

    def _build_result(
        self,
        business_cookies: dict[str, str],
        refresh_cookies: dict[str, str],
    ) -> BootstrapResult:
        if not business_cookies:
            raise _BootstrapError("bootstrap produced no business cookies")
        if "UCID" not in business_cookies or "puzu_lease_token" not in business_cookies:
            raise _BootstrapError("bootstrap cookies missing required fields")

        material = self._serialize_business_material(business_cookies)
        refresh_material = (
            self._serialize_refresh_material(refresh_cookies)
            if refresh_cookies or True
            else None
        )
        expires_at = self._estimate_expires_at()
        return BootstrapResult(
            credential_material=material,
            expires_at=expires_at,
            credential_version=1,
            refresh_material=refresh_material,
        )

    @staticmethod
    def _serialize_business_material(cookies: dict[str, str]) -> bytes:
        return json.dumps(
            {name: cookies[name] for name in _BUSINESS_COOKIE_NAMES if name in cookies},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _deserialize_business_material(material: bytes | None) -> dict[str, str]:
        if not material:
            return {}
        try:
            decoded = json.loads(material.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return {str(k): str(v) for k, v in decoded.items()}

    @staticmethod
    def _serialize_refresh_material(cookies: dict[str, str]) -> bytes:
        return json.dumps(
            {name: cookies[name] for name in _REFRESH_COOKIE_NAMES if name in cookies},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _deserialize_refresh_material(material: bytes | None) -> dict[str, str]:
        if not material:
            return {}
        try:
            decoded = json.loads(material.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return {str(k): str(v) for k, v in decoded.items()}

    def _sso_cookies(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            if cookie.name in _REFRESH_COOKIE_NAMES and cookie.value is not None:
                out[cookie.name] = cookie.value
        return out

    def _seed_cookies(self, url: str, cookies: dict[str, str]) -> None:
        # Per-request ``cookies=`` is deprecated in httpx 0.28+ and does not
        # honour domain scoping. Seeding the client jar keeps the cookies on
        # the domain they belong to, exactly like the browser session we mimic.
        host = urllib.parse.urlsplit(url).netloc
        for name, value in cookies.items():
            self._client.cookies.set(name, value, domain=host)

    @staticmethod
    def _decode_display_name(business_cookies: dict[str, str]) -> str | None:
        raw = business_cookies.get("Lianjia_u_info")
        if not raw:
            return None
        try:
            decoded = urllib.parse.unquote(raw)
            info = json.loads(decoded)
            if isinstance(info, dict):
                return str(info.get("name")) or None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return None

    def _estimate_expires_at(self) -> datetime | None:
        # lianjia_ssid has an observed Max-Age of 1800 seconds. We stay
        # conservative and assume an hour window before the session provider
        # should attempt a refresh triggered by keepalive.
        return self._clock() + timedelta(hours=1)


# -- supporting types -----------------------------------------------------


class _BootstrapError(RuntimeError):
    """Raised when bootstrap/refresh fails in a recoverable way."""


class _BootstrapExpired(_BootstrapError):
    """Raised when qrcode polling exhausts its deadline."""


class _QrRenderer:
    def render(self, payload: str, *, note: str) -> None:  # pragma: no cover - protocol marker
        raise NotImplementedError


class _TerminalQrRenderer(_QrRenderer):
    """Render the qrcode content to the terminal via qrcode_terminal.

    The renderer is intentionally stateless and thread-unsafe — it is only
    called from the bootstrap path which runs synchronously in crm-authd.
    Falls back to a one-line URL print if qrcode_terminal is unavailable.
    """

    def render(self, payload: str, *, note: str) -> None:
        try:
            import qrcode  # type: ignore[import-not-found, import-untyped]
            import qrcode_terminal.qr_terminal as term  # type: ignore[import-not-found, import-untyped]
        except ImportError:
            print(payload)
            print(note)
            return

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        qr.add_data(payload)
        qr.make(fit=True)

        buffer = io.StringIO()
        term.print_ascii(qr, out=buffer)
        print(buffer.getvalue())
        print(note)
        print(f"qrCodeContent: {payload}")
