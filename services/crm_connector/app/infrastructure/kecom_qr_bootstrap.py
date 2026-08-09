from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
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
    qrcode_id: str
    login_ticket_id: str
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
        self._qr = qr_renderer or _default_renderer(self._settings)
        self._clock = clock
        self._sleep = sleep

    # -- CredentialBootstrapProvider ----------------------------------------

    def bootstrap(self) -> BootstrapResult:
        refresh_attempt = 0
        while True:
            handle = self._initialize()
            ordinal = refresh_attempt + 1
            self._qr.render(
                handle.qrcode_content,
                note=(
                    f"请使用 Link/A+/D+/Studio/企微 扫描第 {ordinal} 张二维码登录。\n"
                    "Connector 轮询手机端确认状态……"
                ),
            )
            try:
                confirmed = self._poll_until_confirmed(handle)
            except _BootstrapExpired:
                refresh_attempt += 1
                delay = self._qrcode_refresh_delay(refresh_attempt)
                logger.info(
                    "auth.bootstrap.qrcode_expired refresh_attempt=%s delay_seconds=%s",
                    refresh_attempt,
                    delay,
                )
                print("[*] QR code expired; refreshing automatically ...")
                self._close_qr()
                self._sleep_with_qr_pump(delay)
                continue
            except Exception:
                self._close_qr()
                raise

            # The phone has confirmed the scan. Close the local QR window
            # before completing the ticket exchange so it never lingers over
            # the employee's desktop after a successful scan.
            self._close_qr()
            business_cookies, refresh_cookies = self._exchange_ticket(
                confirmed.service_ticket,
                confirmed.qrcode_id,
                confirmed.login_ticket_id,
            )
            return self._build_result(business_cookies, refresh_cookies)

    def _sleep_with_qr_pump(self, seconds: float) -> None:
        """Keep a local QR dialog responsive while polling in this thread."""
        remaining = max(seconds, 0.0)
        while remaining > 0:
            self._pump_qr()
            step = min(remaining, 0.25)
            self._sleep(step)
            remaining -= step
        self._pump_qr()

    def _pump_qr(self) -> None:
        pump = getattr(self._qr, "pump", None)
        if callable(pump):
            pump()

    def _close_qr(self) -> None:
        close = getattr(self._qr, "close", None)
        if callable(close):
            close()

    def refresh(self, current: ActiveCredential) -> BootstrapResult | None:
        refresh_cookies = self._deserialize_refresh_material(current.refresh_material)
        if not refresh_cookies or "TGC" not in refresh_cookies:
            return None

        # Step 0 — the refresh cookies live on the ke.com domain; seed them into
        # the client's domain-scoped jar so the CAS request carries them without
        # leaking them onto the business domain later.
        self._seed_cookies(self._settings.crm_login_base, refresh_cookies)

        # Step 1 — walk the same CAS chain the browser does: GET the lease business
        # page with the TGC in jar; lease-pz redirects to login.ke.com, CAS mints
        # a service-bound ST, and httpx follows the chain back to lease-pz which
        # plants puzu_lease_token / UCID / csrfSecret.
        business_cookies, refreshed_refresh_cookies = self._establish_business_session()
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
        KeComQrBootstrapProvider._log_confirmed_payload_shape(body)
        if body.get("code") not in (100000, 0):
            raise _BootstrapError(
                f"accountRightInfo rejected material code={body.get('code')} body={body!r}"
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

    def _poll_until_confirmed(
        self, handle: _QrCodeHandle
    ) -> _ConfirmedResult:
        # Real upstream observation (2026-08-06): the CONFIRMED response body
        # is exactly ``{"state": "CONFIRMED", "success": true}`` and carries
        # **no ticket** field. The desktop passport-web flow then completes
        # auth via POST /authentication/authenticate (see _authenticate_exchange).
        # Reference: docs/crm-auth-flow-analysis.md §8.3.
        qrcode_id = handle.qrcode_id
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
                # Some SSO profiles still embed the ticket in the response;
                # prefer the explicit ticket when present, else fall back to
                # the desktop flow: POST /authentication/authenticate with the
                # qrcode id and use serviceTicket.callbackUrl (see
                # _exchange_ticket). Reference: docs/crm-auth-flow-analysis.md
                # §8.3 and the passport-web loginRouter SDK.
                ticket = self._extract_ticket_from_confirmed(payload)
                return _ConfirmedResult(
                    service_ticket=ticket or "",
                    qrcode_id=qrcode_id,
                    login_ticket_id=handle.login_ticket_id,
                    business_cookies={},
                    refresh_cookies=dict(self._sso_cookies()),
                )
            if last_state == "EXPIRED":
                raise _BootstrapExpired("qrcode expired while polling")
            self._sleep_with_qr_pump(self._settings.bootstrap_poll_interval_seconds)
        raise _BootstrapPollTimedOut(f"polling timed out in state={last_state}")

    def _qrcode_refresh_delay(self, refresh_attempt: int) -> float:
        """Bound exponential backoff between terminal QR refreshes."""
        initial = max(self._settings.bootstrap_qrcode_refresh_initial_delay_seconds, 0.0)
        exponent = min(max(refresh_attempt - 1, 0), 5)
        return min(initial * (2**exponent), 30.0)

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
        # Diagnostic: emit a redacted shape of the payload so we can identify
        # the real ticket-bearing field without persisting any ticket value.
        # Only logs key names, JSON type, and short non-sensitive prefixes.
        KeComQrBootstrapProvider._log_confirmed_payload_shape(payload)
        return None

    @staticmethod
    def _log_confirmed_payload_shape(payload: dict[str, Any]) -> None:
        """Redacted one-shot diagnostic; never logs ticket values."""
        try:
            shape: dict[str, str] = {}
            for key, value in payload.items():
                if isinstance(value, str):
                    preview = value[:8].replace("\n", " ").replace("\r", " ")
                    shape[key] = f"str(prefix={preview!r},len={len(value)})"
                elif isinstance(value, (int, float, bool)):
                    shape[key] = f"{type(value).__name__}({value!r})"
                elif isinstance(value, dict):
                    shape[key] = f"dict(keys={list(value.keys())[:8]!r})"
                elif isinstance(value, list):
                    shape[key] = f"list(len={len(value)})"
                else:
                    shape[key] = type(value).__name__
            logger.warning("auth.bootstrap.confirmed_payload_shape shape=%s", shape)
        except Exception:  # pragma: no cover - never fail the bootstrap path
            logger.warning("auth.bootstrap.confirmed_payload_shape_log_failed", exc_info=True)

    def _log_exchange_hop(self, hop: str, response: httpx.Response) -> None:
        """Redacted diagnostic for the ticket-exchange redirect chain."""
        try:
            set_cookies: list[str] = []
            for header_value in response.headers.get_list("set-cookie"):
                name = header_value.split("=", 1)[0].strip()
                set_cookies.append(name)
            location = response.headers.get("location", "")
            body_prefix = ""
            try:
                text = response.text
                body_prefix = text[:200].replace("\n", " ").replace("\r", " ")
            except Exception:
                body_prefix = "<unreadable>"
            logger.info(
                "auth.bootstrap.exchange_hop hop=%s status=%s url=%s set_cookie_names=%s "
                "location_len=%d body_prefix=%r",
                hop,
                response.status_code,
                response.url,
                set_cookies,
                len(location),
                body_prefix,
            )
            if hop == "sdk_confirm" and response.status_code == 200:
                self._dump_exchange_body("sdk_confirm.html", response.text)
            if hop == "lease_landing":
                self._dump_exchange_body("lease_landing_headers.txt", "\n".join(f"{k}: {v}" for k, v in response.headers.multi_items()))
        except Exception:  # pragma: no cover - never fail the bootstrap path
            logger.warning("auth.bootstrap.exchange_hop_log_failed", exc_info=True)

    def _dump_exchange_body(self, filename: str, text: str) -> None:
        """Persist a full hop body for offline protocol analysis. Written into
        the process working directory only (never logged)."""
        try:
            target = self._settings.credential_store_path
            base = str(target)
            if not base:
                base = "."
            if len(text) <= 1_000_000:
                with open(os.path.join(os.path.dirname(base), filename), "w", encoding="utf-8") as fh:
                    fh.write(text)
        except Exception:  # pragma: no cover
            pass

    def _log_cookie_jar(self, marker: str) -> None:
        """Redacted diagnostic: cookie names currently held in the jar."""
        try:
            names = sorted({c.name for c in self._client.cookies.jar if c.name})
            logger.info("auth.bootstrap.cookie_jar marker=%s names=%s", marker, names)
        except Exception:  # pragma: no cover - never fail the bootstrap path
            logger.warning("auth.bootstrap.cookie_jar_log_failed", exc_info=True)

    def _exchange_ticket(
        self,
        service_ticket: str,
        qrcode_id: str = "",
        login_ticket_id: str = "",
    ) -> tuple[dict[str, str], dict[str, str]]:
        # ``service_ticket`` is a CAS ST ticket (``ST-...-ke.com``) when the
        # upstream CONFIRMED response carried one. When it is empty (observed
        # 2026-08-06: CONFIRMED body is exactly ``{state, success}``) we replay
        # the desktop passport-web flow decoded from the loginRouter SDK:
        #   POST /authentication/authenticate
        #     {"service": <crm_service_url>, "context": {"deviceId","sign"},
        #      "version": "2.0", "loginTicketId": <handle.login_ticket_id>,
        #      "accountSystem": "employee", "mainAuthMethodName": "qrcode",
        #      "credential": {"id": <qrcode_id>}}
        #   → Set-Cookie: TGC/TGC_Secure on login.ke.com.
        #
        # The authenticate callback URL's ST is bound to ``crm_service_url``
        # which does NOT match the service string lease-pz constructs for the
        # page the user actually wants (``/rent/house/list?isSaaS=false``), so
        # CAS validation fails and lease-pz returns its JS login page.
        #
        # The faithful CAS flow is the one the browser walks:
        #   1. login.ke.com/login?service=<lease-pz's own /login?gotoURL=...
        #      encoded url> (TGC already in jar via authenticate);
        #   2. login.ke.com sees the TGC → mints an ST bound to that EXACT
        #      service → 302 → lease-pz/login?gotoURL=...&ticket=ST;
        #   3. lease-pz validates the ST (service string matches what it just
        #      built) → Set-Cookie puzu_lease_token / UCID / csrfSecret / ...
        #      → 302 → /rent/house/list (final 200).
        # httpx follow_redirects walks the whole chain because each hop's
        # cookies are scoped to its own domain (TGC stays on ke.com, the lease
        # cookies land on lease-pz).
        if service_ticket.startswith("http://") or service_ticket.startswith("https://"):
            response = self._client.get(service_ticket, follow_redirects=True)
        elif service_ticket:
            # Treat incoming STs as bound to the business CAS client; exchange
            # at the lease-pz login endpoint exactly the way the chain above
            # would build it. (Refresh path provides a pre-minted ST.)
            response = self._client.get(
                self._settings.crm_service_url,
                params={"ticket": service_ticket},
                follow_redirects=True,
            )
        else:
            self._authenticate_exchange(qrcode_id, login_ticket_id)
            business, refresh = self._establish_business_session()
            return business, refresh
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
        self._log_cookie_jar("post_exchange")

        # Defensive tail: if the chain collapsed without planting puzu_lease_token
        # (e.g. a session cookie scope surprise), capture the landing Set-Cookie
        # headers so we can diagnose offline rather than retry blind.
        if "puzu_lease_token" not in business:
            self._dump_exchange_body(
                "lease_landing_headers.txt",
                "\n".join(f"{k}: {v}" for k, v in response.headers.multi_items()),
            )

        if not business or "puzu_lease_token" not in business:
            raise _BootstrapError(
                f"ticket exchange produced no business cookies (business={sorted(business)} names={sorted(all_cookies)})"
            )
        return business, refresh

    def _establish_business_session(self) -> tuple[dict[str, str], dict[str, str]]:
        # Shared by bootstrap and refresh. Probed 2026-08-06: lease-pz's
        # /rent/house/list SPA NEVER server-redirects to CAS (every header /
        # cookie variant returns 200 and clears puzu_lease_token); the redirect
        # to login.ke.com observed in the browser is JS-driven. So instead of
        # GETting the list page we call the CAS front channel directly with the
        # exact service string lease-pz's JS builds:
        #   GET {crm_login_base}/login?service=<origin>/login?gotoURL=<enc>
        # with the TGC in the jar → CAS validates TGC → 302 → lease-pz/login
        # ?gotoURL=...&ticket=ST → lease-pz validates the ST against its own
        # service string → Set-Cookie puzu_lease_token / UCID / csrfSecret →
        # 302 → /rent/house/list?isSaaS=false (final 200, SPA).
        #
        # Docs §5.1 + confirmed 2026-08-06: that single lease-pz hop is NOT
        # enough for the business APIs (accountRightInfo / houseList return
        # 100001 请重新登录 even with a full puzu_lease_token jar). The browser
        # SPA then fires a SECOND CAS-hop into house.link.lianjia.com/shiro-cas:
        #   GET {crm_login_base}/login?service=house.link.lianjia.com/shiro-cas
        # → 302 → house.link.lianjia.com/shiro-cas?ticket=ST → house.link plants
        # `saas_token` (and HOUSEJSESSIONID). With saas_token in the jar the
        # lease-pz business endpoints immediately return code=100000. Both hops
        # use the same TGC (one QR scan) so this is zero-extra-scan.
        business_service = (
            f"{self._settings.crm_business_origin}/login"
            "?gotoURL=%25252Frent%25252Fhouse%25252Flist%25253FisSaaS%25253Dfalse"
        )
        response = self._client.get(
            f"{self._settings.crm_login_base}/login",
            params={"service": business_service},
            follow_redirects=True,
        )
        self._log_exchange_hop("lease_cas_chain", response)
        if response.status_code >= 400:
            raise _BootstrapError(
                f"business session establishment failed status={response.status_code}"
            )
        # Docs §5.1: second CAS hop into house.link shiro-cas to plant saas_token
        # (the cookie that lease-pz business endpoints /api/houseList/* require;
        # without it they return 100001 请重新登录 even with a valid puzu_lease_token).
        shiro_response = self._client.get(
            f"{self._settings.crm_login_base}/login",
            params={"service": "https://house.link.lianjia.com/shiro-cas"},
            follow_redirects=True,
        )
        self._log_exchange_hop("house_link_shiro_cas", shiro_response)
        if shiro_response.status_code >= 400:
            raise _BootstrapError(
                f"house.link shiro-cas hop failed status={shiro_response.status_code}"
            )
        all_cookies: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            if cookie.name is not None and cookie.value is not None:
                all_cookies[cookie.name] = cookie.value
        business = {name: all_cookies[name] for name in _BUSINESS_COOKIE_NAMES if name in all_cookies}
        refresh = {name: all_cookies[name] for name in _REFRESH_COOKIE_NAMES if name in all_cookies}
        self._log_cookie_jar("post_business_session")
        token_value = business.get("puzu_lease_token", "")
        if "puzu_lease_token" not in business or not token_value:
            self._dump_exchange_body(
                "lease_landing_headers.txt",
                "\n".join(f"{k}: {v}" for k, v in response.headers.multi_items()),
            )
        if not business or "puzu_lease_token" not in business or not token_value:
            raise _BootstrapError(
                f"business session produced no business cookies (business={sorted(business)} names={sorted(all_cookies)})"
            )
        return business, refresh

    def _authenticate_exchange(self, qrcode_id: str, login_ticket_id: str) -> None:
        # Desktop passport-web flow on CONFIRMED (loginRouter SDK, 2026-08-06):
        # POST /authentication/authenticate exchanges the confirmed qrcode for
        # an SSO TGT (Set-Cookie: TGC/TGC_Secure on login.ke.com) which the
        # subsequent /login?service= front-channel hop will use to mint the
        # service ticket. The authenticate body also carries a callbackUrl,
        # which is NOT the working cookie path (observations show it returns a
        # JS login page with no Set-Cookie on the business domain).
        auth_response = self._client.post(
            f"{self._settings.crm_login_base}/authentication/authenticate",
            json={
                "service": self._settings.crm_service_url,
                "context": {"deviceId": "default", "sign": "default"},
                "version": "2.0",
                "loginTicketId": login_ticket_id,
                "accountSystem": "employee",
                "mainAuthMethodName": "qrcode",
                "credential": {"id": qrcode_id},
            },
            follow_redirects=False,
        )
        self._log_exchange_hop("authenticate", auth_response)
        self._log_cookie_jar("post_authenticate")
        if auth_response.status_code >= 400:
            raise _BootstrapError(
                f"authenticate failed status={auth_response.status_code}"
            )
        code = self._extract_authenticate_code(auth_response)
        if code is not None and code not in (100000, 0, "100000", "0", "PASS", "pass"):
            raise _BootstrapError(
                f"authenticate rejected credential code={code} body={auth_response.text[:500]!r}"
            )

    def _extract_authenticate_code(self, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except json.JSONDecodeError:
            return None
        if isinstance(body, dict):
            return body.get("code", body.get("status"))
        return None

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
    """Raised when the upstream explicitly reports the QR code expired."""


class _BootstrapPollTimedOut(_BootstrapError):
    """Raised when polling makes no terminal progress before its deadline."""


class _QrRenderer:
    def render(self, payload: str, *, note: str) -> None:  # pragma: no cover - protocol marker
        raise NotImplementedError

    def pump(self) -> None:
        """Process pending UI events while the bootstrap poller is waiting."""

    def close(self) -> None:
        """Release any local QR presentation after the scan completes."""


class _TerminalQrRenderer(_QrRenderer):
    """Render the qrcode content to the terminal via qrcode_terminal.

    The renderer is intentionally stateless and thread-unsafe — it is only
    called from the bootstrap path which runs synchronously in crm-authd.
    Emits a clear local error if the terminal QR renderer is unavailable.
    """

    def render(self, payload: str, *, note: str) -> None:
        try:
            import qrcode  # type: ignore[import-not-found, import-untyped]
            import qrcode_terminal.qr_terminal as term  # type: ignore[import-not-found, import-untyped]
        except ImportError:
            print("[!] Terminal QR renderer is unavailable; use the Windows login dialog.")
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


class _PopupQrRenderer(_QrRenderer):
    """A self-contained Windows QR dialog owned by ``crm-authd``.

    The previous renderer opened a PNG in the user's default image viewer,
    which could neither show login progress nor close itself after the phone
    confirmed the scan. This renderer keeps a small Tk dialog in the CLI
    thread and lets the polling loop pump its events. No QR image or payload
    is written to disk or echoed to the terminal.
    """

    def __init__(self) -> None:
        self._root: Any | None = None
        self._image_label: Any | None = None
        self._note_label: Any | None = None
        self._photo: Any | None = None
        self._tk: Any | None = None
        self._fallback = _TerminalQrRenderer()

    def render(self, payload: str, *, note: str) -> None:
        try:
            import tkinter as tk
            from PIL import ImageTk  # type: ignore[import-not-found]
            import qrcode  # type: ignore[import-not-found, import-untyped]
        except ImportError:
            print("[*] Desktop QR window is unavailable; rendering in this terminal.")
            self._fallback.render(payload, note=note)
            return

        try:
            if self._root is None:
                self._create_window(tk)
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=3,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            image = qr.make_image(fill_color="#111827", back_color="white").get_image()
            self._photo = ImageTk.PhotoImage(image)
            self._image_label.configure(image=self._photo)
            self._note_label.configure(text=note)
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)
            self._root.after(250, lambda: self._root and self._root.attributes("-topmost", False))
            self.pump()
        except Exception as exc:  # pragma: no cover - desktop-specific fallback
            logger.warning("auth.qr_popup.unavailable class=%s", exc.__class__.__name__)
            self.close()
            self._fallback.render(payload, note=note)

    def pump(self) -> None:
        if self._root is None:
            return
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:  # pragma: no cover - user closed the native window
            self._clear_window()

    def close(self) -> None:
        if self._root is None:
            return
        try:
            self._root.destroy()
        except Exception:  # pragma: no cover - native window already closed
            pass
        finally:
            self._clear_window()

    def _create_window(self, tk: Any) -> None:
        self._tk = tk
        root = tk.Tk()
        root.title("CRM 扫码登录")
        root.configure(bg="#F8FAFC")
        root.resizable(False, False)
        root.geometry("430x570")
        root.protocol("WM_DELETE_WINDOW", root.iconify)

        container = tk.Frame(root, bg="#F8FAFC", padx=28, pady=24)
        container.pack(fill="both", expand=True)
        tk.Label(
            container,
            text="扫码登录 CRM",
            font=("Microsoft YaHei UI", 18, "bold"),
            bg="#F8FAFC",
            fg="#111827",
        ).pack()
        tk.Label(
            container,
            text="请使用 Link、A+、D+、Studio 或企业微信扫码",
            font=("Microsoft YaHei UI", 10),
            bg="#F8FAFC",
            fg="#64748B",
            pady=8,
        ).pack()
        card = tk.Frame(container, bg="white", padx=18, pady=18, highlightthickness=1)
        card.pack(pady=8)
        self._image_label = tk.Label(card, bg="white")
        self._image_label.pack()
        self._note_label = tk.Label(
            container,
            justify="center",
            wraplength=350,
            font=("Microsoft YaHei UI", 10),
            bg="#F8FAFC",
            fg="#334155",
            pady=12,
        )
        self._note_label.pack()
        tk.Label(
            container,
            text="手机确认后，此窗口会自动关闭。",
            font=("Microsoft YaHei UI", 9),
            bg="#F8FAFC",
            fg="#94A3B8",
        ).pack(pady=(4, 0))
        self._root = root

    def _clear_window(self) -> None:
        self._root = None
        self._image_label = None
        self._note_label = None
        self._photo = None
        self._tk = None


def _default_renderer(settings: Settings) -> _QrRenderer:
    """Pick the best QR renderer for this host.

    Windows hosts default to the popup renderer because the terminal
    renderer's Unicode block characters break under the default cp1252
    console encoding; other hosts keep the terminal ASCII renderer.
    """
    if sys.platform == "win32":
        return _PopupQrRenderer()
    return _TerminalQrRenderer()
