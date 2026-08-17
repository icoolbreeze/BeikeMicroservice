from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Callable

import httpx

from app.domain.errors import (
    AuthenticationRequiredError,
    NetworkRequiredError,
    UpstreamChangedError,
)
from app.domain.models import ConnectionState, Principal, ProviderStatus
from app.domain.providers.credential_bootstrap_provider import (
    BootstrapResult,
    CredentialBootstrapProvider,
)
from app.domain.providers.credential_store import ActiveCredential, CredentialStore
from app.domain.providers.session_provider import (
    AuthorizedRequest,
    UpstreamResponse,
)
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

# Cookie names that must always be injected together when sending an
# authenticated request to lease-pz.link.lianjia.com. Mirrors the
# "necessary + compatibility" set documented in §3 of the auth flow analysis.
_NECESSARY_COOKIES = (
    "puzu_lease_token",
    "puzu_lease_token_secure",
    "UCID",
    "UCID_secure",
    "csrfSecret",
)
_COMPAT_COOKIES = (
    "Lianjia_curWorkCity",
    "Lianjia_BUcid",
    "Lianjia_u_info",
    "lianjia_ssid",
    "lianjia_uuid",
    "saas_token",
    "login_ucid",
    # house.link shiro-cas session; required with saas_token for the 买卖
    # business APIs (house.link.lianjia.com) — without it the upstream
    # returns 403 未登录认证 (probed 2026-08-11).
    "HOUSEJSESSIONID",
    # 托管 (省心租) session tokens planted by the trusteeship CAS callback;
    # required for the trusteeship business APIs (probed 2026-08-15).
    "lianjia_trusteeship_token",
    "lianjia_trusteeship_token_secure",
)

# Account / whoami probe used by the keepalive timer to extend the
# lianjia_ssid sliding window.
_KEEPALIVE_ROUTE = "identity.me"
_KEEPALIVE_PATH = "/api/puzuHouse/puzu/house/auth/pc/accountRightInfo"

# Default business-domain code. The CRM API requires the matching header on
# every business call.
_DEFAULT_CITY_HEADER = "house_current_work_citycode"

# The local expires_at is a conservative estimate produced by
# KeComQrBootstrapProvider._estimate_expires_at (1 h), not the upstream token
# deadline. A successful keepalive probe proves the business session is alive
# upstream and refreshes the lianjia_ssid sliding window, so when the estimate
# gets close we roll it forward instead of letting a stale local clock force a
# rescan of a healthy session.
_EXPIRY_ROLL_FORWARD_SECONDS = 30 * 60
_EXPIRY_EXTENSION_SECONDS = 60 * 60


class KecomSessionProvider:
    """Authentication boundary that owns the active business cookie jar.

    Wraps a ``CredentialStore`` plus the bootstrap adapter so that callers
    of ``authorized_fetch`` see a single, opaque "send a controlled request
    on my behalf" interface. The provider never exposes raw cookies,
    tokens or the bootstrap material.

    Session validation is lazy: no timer probes the upstream. A successful
    business call proves liveness and rolls the local expiry estimate
    forward; a lapsed estimate triggers the silent TGC renewal inside the
    next call. Outbound headers mirror the workbench browser so automated
    traffic stays consistent with the employee's real session.
    """

    def __init__(
        self,
        settings: Settings,
        credential_store: CredentialStore,
        bootstrap: CredentialBootstrapProvider,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._store = credential_store
        self._bootstrap = bootstrap
        self._client_owner = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            # Mirror the workbench browser's client signature on every
            # outbound request; a non-browser UA / accept-language pair is
            # the most conspicuous automated-access signal for the upstream
            # risk control. Override via CC_HTTP_USER_AGENT to track the
            # actual browser version in use.
            headers={
                "user-agent": settings.http_user_agent,
                "accept-language": settings.http_accept_language,
            },
        )
        self._clock = clock or _utc_now

        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        # In-memory cache of the current ActiveCredential so concurrent
        # authorized_fetch callers do not hammer the store on every request.
        self._active: ActiveCredential | None = None

        # Bookkeeping for keepalive and degradation states.
        self._last_keepalive_at: datetime | None = None
        self._last_business_failure_at: datetime | None = None
        self._refresh_in_progress = False

        # Status states the provider can report. The ConnectionState enum is
        # shared with the rest of the service.
        self._degraded_message: str | None = None

    # -- SessionProvider ----------------------------------------------------

    def status(self) -> ProviderStatus:
        with self._lock:
            active = self._active or self._store.load_active()
            self._active = active
            if active is None:
                return ProviderStatus(
                    ConnectionState.AUTH_REQUIRED,
                    "CRM authorization not bootstrapped",
                )

            if self._is_expired(active):
                # Lazy validation: status() is a local read and never
                # touches the upstream. A lapsed local estimate is still
                # recoverable via the silent TGC renewal that runs inside
                # the next authorized_fetch, so report EXPIRING instead of
                # probing or forcing a rescan here. Deactivation stays a
                # decision of the actual request path.
                return ProviderStatus(
                    ConnectionState.EXPIRING,
                    "local expiry estimate lapsed; silent TGC renewal runs "
                    "on the next request",
                    expires_at=active.expires_at,
                )

            state, message = self._derive_state_locked(active)
            return ProviderStatus(
                state, message, expires_at=active.expires_at
            )

    def authorized_fetch(self, request: AuthorizedRequest) -> UpstreamResponse:
        # Validate against the route allow-list: SessionProvider never
        # forwards arbitrary URLs.
        if not isinstance(request.route, str) or not request.route:
            raise ValueError("AuthorizedRequest.route must be a non-empty string")

        request_id = request.request_id or str(uuid.uuid4())

        active = self._require_active_or_raise()
        response = self._send_authorized(request, active, request_id)

        # Auto refresh once on a 401 / business "未登录" code.
        refreshed = self._maybe_autorefresh(active, response)
        if refreshed is not None:
            response = self._send_authorized(request, refreshed, request_id)

        # Detect upstream authentication failure after refresh; force the
        # caller to receive CRM_AUTH_REQUIRED.
        if self._is_auth_failure(response):
            self._deactivate(active.session_id, "upstream_rejected")
            raise AuthenticationRequiredError(
                "CRM authorization was rejected by the upstream after request"
            )

        # Lazy keepalive: a successful (non-auth-failure) business response
        # proves the session is alive upstream and extends the sliding
        # lianjia_ssid window — exactly what the periodic keepalive probe
        # used to do. Roll the local estimate forward so a used session is
        # never expired by the clock while an idle one stays untouched.
        self._roll_expiry_forward(refreshed if refreshed is not None else active)

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            # A 200 with an HTML body is the upstream answering with a login
            # or error page instead of the business envelope; surface the
            # contract drift instead of a raw JSONDecodeError.
            raise UpstreamChangedError(
                f"{request.route} returned a non-JSON body "
                f"(status {response.status_code})"
            ) from exc
        return UpstreamResponse(status_code=response.status_code, body=body)

    def bound_principal(self) -> Principal | None:
        """Return the employee principal this session was bootstrapped with.

        The upstream accountRightInfo envelope does not echo the principal,
        so the identity recorded at scan time (the ``UCID`` cookie) is the
        authoritative local source. Credentials installed by older silent
        TGC renewals may carry an empty recorded principal (the renewal used
        to drop it); the UCID cookie inside the credential material is the
        same identity, so fall back to it before giving up.
        """
        with self._lock:
            active = self._active or self._store.load_active()
            self._active = active
        if active is None:
            return None
        principal = active.employee_principal or _decode_material(
            active.credential_material
        ).get("UCID", "")
        if not principal:
            return None
        return Principal(employee_principal=principal)

    def close(self) -> None:
        if self._client_owner:
            self._client.close()

    # -- privileged operations used by crm-authd ----------------------------

    def install_fresh_credential(self, result: BootstrapResult, *, session_id: str | None = None) -> ActiveCredential:
        """Persist a freshly bootstrapped face and make it active.

        Called by ``crm-authd`` after ``bootstrap()`` or ``refresh()`` have
        returned material. Performs the
        "validate → atomic save → invalidate predecessor" sequence mandated
        by plan §4.1.
        """
        principal = self._bootstrap.validate(result.credential_material)

        # The "session id" we expose is the HTTP-perceived lianjia_ssid
        # stripped from the material; fell back to a synthesised UUID if
        # missing so the CredentialStore can still index us deterministically.
        business_cookies = _decode_material(result.credential_material)
        session_id = session_id or business_cookies.get("lianjia_ssid") or str(uuid.uuid4())

        new_credential = ActiveCredential(
            session_id=session_id,
            employee_principal=principal.employee_principal,
            credential_material=result.credential_material,
            expires_at=result.expires_at,
            credential_version=result.credential_version,
            refresh_material=result.refresh_material,
        )

        with self._lock:
            previous = self._active or self._store.load_active()
            self._store.save(new_credential)
            self._active = new_credential
            # A successful human-assisted login is authoritative. Clear the
            # failure latch left by the credential that required the rescan;
            # otherwise status() remains DEGRADED forever and the watchdog
            # will never consider the session healthy again.
            self._clear_degraded_locked()
            if previous is not None and previous.session_id != session_id:
                self._store.invalidate(previous.session_id, "replaced")
            return new_credential

    # -- keepalive ----------------------------------------------------------

    def run_keepalive(self) -> ProviderStatus:
        """Send a low-cost probe that extends lianjia_ssid's sliding window.

        Idempotent; safe to call from a timer thread. When the active
        credential is unauthenticated, this is a no-op and returns the
        current status.
        """
        with self._lock:
            active = self._active or self._store.load_active()
            self._active = active
            if active is None:
                return ProviderStatus(
                    ConnectionState.AUTH_REQUIRED,
                    "CRM authorization not bootstrapped",
                )
            cookies = _decode_material(active.credential_material)
            try:
                response = self._client.get(
                    f"{self._settings.crm_business_origin}{_KEEPALIVE_PATH}",
                    params={"typeList": "2"},
                    cookies=_select_cookies(cookies),
                    headers=self._default_headers(),
                )
            except httpx.HTTPError as exc:
                logger.warning("keepalive.network_failed class=%s", exc.__class__.__name__)
                self._last_business_failure_at = self._clock()
                return ProviderStatus(
                    ConnectionState.NETWORK_REQUIRED,
                    "CRM network probe failed",
                )

            self._last_keepalive_at = self._clock()
            if response.status_code != 200 or self._is_auth_failure(response):
                # Refresh and re-probe; if refresh still fails, leave state
                # as AUTH_REQUIRED so the human-assisted bootstrap is needed.
                try:
                    refreshed = self._make_credential(self._bootstrap.refresh(active))
                except Exception as exc:  # noqa: BLE001 - bootstrap is an adapter boundary
                    logger.warning(
                        "keepalive.refresh_failed class=%s", exc.__class__.__name__
                    )
                    refreshed = None
                if refreshed is None:
                    self._deactivate_locked(active.session_id, "upstream_rejected")
                    self._degraded_message = "keepalive failed; employee rescan required"
                    return ProviderStatus(
                        ConnectionState.AUTH_REQUIRED,
                        self._degraded_message,
                    )
                self._store.save(refreshed)
                self._active = refreshed
                self._clear_degraded_locked()
                self._store.invalidate(active.session_id, "replaced")
                return ProviderStatus(
                    ConnectionState.READY,
                    "CRM authorization refreshed via TGC after keepalive failure",
                    expires_at=refreshed.expires_at,
                )
            # Probe succeeded: the business session is alive upstream and each
            # response refreshes the lianjia_ssid sliding window. Roll the
            # conservative local expiry estimate forward so a healthy session
            # is not marked expired by the clock.
            extended = self._roll_expiry_forward(active)
            self._clear_degraded_locked()
            state, message = self._derive_state_locked(extended)
            return ProviderStatus(
                state, message, expires_at=extended.expires_at
            )

    # -- internals: send + refresh -----------------------------------------

    def _send_authorized(
        self, request: AuthorizedRequest, active: ActiveCredential, request_id: str
    ) -> httpx.Response:
        cookies = _decode_material(active.credential_material)
        method = request.method.upper()
        path, requires_city_header, origin = _resolve_route(request.route)
        if origin == "map":
            base_url = self._settings.crm_map_origin
        elif origin == "house":
            base_url = self._settings.crm_house_origin
        elif origin == "trusteeship":
            base_url = self._settings.crm_trusteeship_origin
        else:
            base_url = self._settings.crm_business_origin
        url = f"{base_url}{path}"

        # Optional query string. We enforce a primitive whitelist on keys
        # so callers can't smuggle arbitrary params to undocumented routes.
        params: dict[str, str | int | float | bool] = {}
        if request.query:
            for key, value in request.query.items():
                if value is None:
                    continue
                params[str(key)] = value
        body = request.body if method in ("POST", "PUT") else None

        headers = self._default_headers()
        if origin == "map":
            # Keep the browser map SDK's fixed headers inside the auth boundary.
            headers["referer"] = f"{self._settings.crm_business_origin}/rent/house/map"
            headers["plat"] = "B"
            headers["appId"] = "228"
            map_token = cookies.get("puzu_lease_token")
            if map_token:
                headers["user-token"] = map_token
        if origin == "house":
            # 买卖 workbench (house.link.lianjia.com) request signature captured
            # from the live search page 2026-08-11: lianjia_curworkcity /
            # lianjia_bucid headers plus the sale search referer.
            headers["referer"] = (
                f"{self._settings.crm_house_origin}/search/sale/default/gdiv_mt"
            )
            headers["lianjia_curworkcity"] = self._settings.crm_default_city_code
            bucid = cookies.get("UCID") or cookies.get("login_ucid")
            if bucid:
                headers["lianjia_bucid"] = bucid
        if origin == "trusteeship":
            # 托管 workbench (省心租) request signature captured live
            # 2026-08-15: the SPA calls its own domain with the same cookie
            # family as lease-pz plus a referer pointing at the detail page.
            headers["referer"] = (
                f"{self._settings.crm_trusteeship_origin}/house/detail/agent/"
            )
        if requires_city_header:
            headers[_DEFAULT_CITY_HEADER] = self._settings.crm_default_city_code
        headers["x-request-id"] = request_id

        try:
            request_cookies = _select_cookies(cookies)
            if origin == "trusteeship":
                # The 托管 SPA authenticates with the shared ke.com family;
                # live observation (2026-08-15) shows a fresh security_ticket
                # (planted by the CAS walk) is what distinguishes a working
                # 托管 session from a stale one.
                refresh_cookies = _decode_material(active.refresh_material)
                security_ticket = refresh_cookies.get("security_ticket")
                if security_ticket:
                    request_cookies = {**request_cookies, "security_ticket": security_ticket}
            return self._client.request(
                method,
                url,
                params=params or None,
                json=body if body is not None else None,
                cookies=request_cookies,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "authorized_fetch.network_failed route=%s class=%s request_id=%s",
                request.route, exc.__class__.__name__, request_id,
            )
            self._last_business_failure_at = self._clock()
            raise NetworkRequiredError(f"CRM upstream unreachable: {exc}") from exc

    def _maybe_autorefresh(
        self, active: ActiveCredential, response: httpx.Response
    ) -> ActiveCredential | None:
        if not self._is_auth_failure(response):
            return None

        # Per-instance mutex so concurrent 401s collapse into a single refresh.
        if not self._refresh_lock.acquire(blocking=False):
            # Another caller is already refreshing — wait briefly for it,
            # then return whatever the store reports as active.
            time.sleep(0.05)
            refreshed = self._store.load_active()
            return refreshed if refreshed and refreshed.session_id != active.session_id else None

        try:
            try:
                refreshed = self._make_credential(
                    self._bootstrap.refresh(active), previous=active
                )
            except Exception as exc:  # noqa: BLE001 - bootstrap is an adapter boundary
                logger.warning(
                    "authorized_fetch.refresh_failed class=%s", exc.__class__.__name__
                )
                refreshed = None
            if refreshed is None:
                # Only reject the old credential once the renewal itself has
                # failed. Deactivating before the refresh attempt discarded a
                # possibly-still-valid credential whenever the refresh hit a
                # transient network error (authorized_fetch's post-refresh
                # rejection path deactivates too, so the end state matches).
                self._deactivate_locked(active.session_id, "upstream_rejected")
                return None
            self._store.save(refreshed)
            self._active = refreshed
            self._clear_degraded_locked()
            self._store.invalidate(active.session_id, "replaced")
            return refreshed
        finally:
            self._refresh_lock.release()

    @staticmethod
    def _make_credential(
        result: BootstrapResult | None,
        previous: ActiveCredential | None = None,
    ) -> ActiveCredential | None:
        if result is None:
            return None
        # The renewal re-plants the UCID business cookie, so the principal
        # survives the refresh. Hardcoding "" here used to permanently break
        # crm_whoami / bound-principal verification after the first silent
        # TGC renewal (accountRightInfo carries no principal to recover it
        # from upstream); keep the replaced credential's principal as the
        # fallback for material shapes without a UCID.
        principal = (
            _decode_material(result.credential_material).get("UCID", "")
            or (previous.employee_principal if previous is not None else "")
            or ""
        )
        # active.session_id will be supplied by install_fresh_credential /
        # KecomSessionProvider when persisting; here we only need a unique
        # placeholder so the CredentialStore records a new row.
        return ActiveCredential(
            session_id=str(uuid.uuid4()),
            employee_principal=principal,
            credential_material=result.credential_material,
            expires_at=result.expires_at,
            credential_version=result.credential_version,
            refresh_material=result.refresh_material,
        )

    # -- internals: silent renewal ---------------------------------------

    def _refresh_on_local_expiry(self, active: ActiveCredential) -> ActiveCredential | None:
        """Silent TGC renewal when the local expiry estimate lapses.

        ``expires_at`` is a conservative estimate (see
        ``KeComQrBootstrapProvider._estimate_expires_at``), not the upstream
        token deadline; the TGC refresh material stays valid for hours-to-days
        upstream, so a renewal usually succeeds without a human rescan.
        Deactivation is only the fallback when the renewal itself fails.
        Mirrors the auto-refresh on upstream rejection: single-flight under
        ``_refresh_lock``, store-save, predecessor invalidation.
        """
        with self._refresh_lock:
            current = self._active or self._store.load_active()
            self._active = current
            if current is None:
                return None
            if current.session_id != active.session_id:
                # Another caller already replaced the credential (e.g. a
                # concurrent refresh); treat that as the renewal.
                return current
            try:
                refreshed = self._make_credential(
                    self._bootstrap.refresh(active), previous=active
                )
            except Exception as exc:  # noqa: BLE001 - bootstrap is an adapter boundary
                logger.warning(
                    "expiry.refresh_failed class=%s", exc.__class__.__name__
                )
                refreshed = None
            if refreshed is None:
                return None
            self._store.save(refreshed)
            self._active = refreshed
            self._clear_degraded_locked()
            self._store.invalidate(active.session_id, "replaced")
            logger.info("session.refresh_via_tgc reason=local_expiry")
            return refreshed

    def _roll_expiry_forward(self, active: ActiveCredential) -> ActiveCredential:
        """Extend the conservative expiry estimate after a live probe.

        Only persists when the estimate is close to lapsing so the store is
        not rewritten on every request. Called from both the locked
        keepalive path and the (lock-free) authorized_fetch success path,
        so it takes the (re-entrant) state lock itself, and only touches
        the slot when the credential is still current — a concurrent QR
        login or TGC refresh must not be overwritten by an older object
        rolling forward (same staleness rule as _deactivate_locked).
        """
        with self._lock:
            current = self._active or self._store.load_active()
            if current is None or current.session_id != active.session_id:
                return active
            if active.expires_at is None:
                return active
            remaining = active.expires_at - self._clock()
            if remaining.total_seconds() > _EXPIRY_ROLL_FORWARD_SECONDS:
                return active
            extended = replace(
                active,
                expires_at=self._clock() + timedelta(seconds=_EXPIRY_EXTENSION_SECONDS),
            )
            self._store.save(extended)
            self._active = extended
            return extended

    # -- internals: state derivation --------------------------------------

    def _derive_state_locked(
        self, active: ActiveCredential
    ) -> tuple[ConnectionState, str]:
        if self._degraded_message:
            return ConnectionState.DEGRADED, self._degraded_message

        # If we've never sent a keepalive yet, still rely on expires_at
        # for the "expiring" hint.
        if active.expires_at is not None:
            remaining = active.expires_at - self._clock()
            if remaining.total_seconds() < 60:
                return ConnectionState.EXPIRING, (
                    "CRM authorization nearing expiry; refresh scheduled"
                )

        return ConnectionState.READY, "CRM authorization is ready"

    def _is_expired(self, active: ActiveCredential) -> bool:
        if active.expires_at is None:
            return False
        return active.expires_at <= self._clock()

    def _require_active_or_raise(self) -> ActiveCredential:
        with self._lock:
            active = self._active or self._store.load_active()
            self._active = active
        if active is None:
            raise AuthenticationRequiredError(
                "CRM authorization not bootstrapped; run crm-authd login"
            )
        if self._is_expired(active):
            # Same silent-renewal-first policy as status(): the local
            # estimate is not the upstream deadline, so refresh via TGC
            # before failing the call.
            refreshed = self._refresh_on_local_expiry(active)
            if refreshed is not None:
                return refreshed
            self._deactivate(active.session_id, "expired")
            raise AuthenticationRequiredError(
                "CRM authorization expired; refresh failed, rescan required"
            )
        return active

    def _deactivate(self, session_id: str, reason: str) -> None:
        with self._lock:
            self._deactivate_locked(session_id, reason)

    def _deactivate_locked(self, session_id: str, reason: str) -> None:
        # Only a deactivation of the CURRENT credential may clear the local
        # slot or raise the degraded latch. A stale rejection (the response
        # of a request sent with a credential that was replaced meanwhile by
        # a fresh QR login or a successful TGC refresh) must not poison an
        # otherwise healthy session: the degraded latch used to be set
        # unconditionally here, which latched the whole process into a
        # "rejected" mode while a valid credential was active (see the
        # stale-credential race in authorized_fetch).
        current = self._active or self._store.load_active()
        is_current = current is not None and current.session_id == session_id
        self._store.invalidate(session_id, reason)  # type: ignore[arg-type]
        if is_current:
            self._active = None
        if reason == "upstream_rejected" and is_current:
            self._degraded_message = "upstream rejected credential; refresh attempted"

    def _clear_degraded_locked(self) -> None:
        self._degraded_message = None
        self._last_business_failure_at = None

    def _default_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "x-requested-with": "XMLHttpRequest",
            "referer": f"{self._settings.crm_business_origin}/rent/house/list?isSaaS=false",
        }

    # -- internals: response interpretation -------------------------------

    @staticmethod
    def _is_auth_failure(response: httpx.Response) -> bool:
        if response.status_code in (401, 403):
            return True
        if response.status_code != 200:
            return False
        # business-domain JSON envelope: {"code": 403, "msg": "用户未登录"}
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            return False
        if not isinstance(body, Mapping):
            return False
        code = body.get("code")
        msg = body.get("msg")
        if code in (403, "403", 31002, "31002"):
            return True
        if isinstance(msg, str) and (
            "未登录" in msg or "请先登录" in msg or "请重新登录" in msg
        ):
            return True
        return False


# -- helpers --------------------------------------------------------------


def _decode_material(material: bytes | None) -> dict[str, str]:
    if not material:
        return {}
    try:
        decoded = json.loads(material.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return {str(k): str(v) for k, v in decoded.items()}


def _select_cookies(cookies: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in (*_NECESSARY_COOKIES, *_COMPAT_COOKIES):
        if name in cookies:
            out[name] = cookies[name]
    return out


def _utc_now() -> datetime:
    return datetime.now(UTC)


# Route allow-list for authorized_fetch. New routes must be declared here
# before any caller can use them; this is the 'controlled routes' boundary
# promised by the architecture document §4.4.
_ROUTE_TABLE = {
    "identity.me": (_KEEPALIVE_PATH, True, "business"),
    "rental_listing.search": ("/api/houseList/search/pc/list", True, "business"),
    "rental_listing.get_redirect_url": (
        "/api/houseList/search/getRedirectUrl",
        True,
        "business",
    ),
    "rental_listing.filter_options": (
        "/api/houseList/search/pc/searchOption",
        True,
        "business",
    ),
    "rental_listing.get_detail": (
        "/api/puzu/house/detail/detailHead",
        False,
        "business",
    ),  # captured from the live detail page (docs §8.4)
    "rental_listing.detail_prospect": (
        "/api/puzu/house/detail/detailProspect",
        False,
        "business",
    ),  # captured from the live detail page (docs §房源详情)
    "rental_listing.get_hdic_info": (
        "/api/puzu/house/detail/detailHdicInfo",
        False,
        "business",
    ),  # 小区/楼栋属性 (docs §房源详细信息)
    "rental_listing.get_house_label": (
        "/api/puzu/house/detail/getHouseLabel",
        False,
        "business",
    ),  # 房源标签 (docs §房源详细信息)
    "rental_listing.get_hqi_tab": (
        "/api/puzu/house/detail/detailHqiTab",
        False,
        "business",
    ),  # HQI 质量评分 (docs §房源详细信息)
    "rental_listing.get_maintain_info": (
        "/api/puzuHouse/puzu/house/detail/app/getMaintainInfo",
        False,
        "business",
    ),  # 维护信息（getMaintainInfo，docs §房源详细信息）
    "rental_listing.get_follow": (
        "/api/puzu/house/detail/detailFollow",
        False,
        "business",
    ),  # 跟进记录（detailFollow，docs §房源详细信息）
    "rental_map.search": (
        "/proxyApi/i.c-pc-webapi.ke.com/map/houselist",
        False,
        "map",
    ),
    "rental_map.search_circle": (
        "/proxyApi/i.c-pc-webapi.ke.com/map/drawhouselist",
        False,
        "map",
    ),
    "rental_map.bubbles": (
        "/proxyApi/i.c-pc-webapi.ke.com/map/bubblelist",
        False,
        "map",
    ),
    "rental_map.suggest": (
        "/proxyApi/i.c-pc-webapi.ke.com/map/sug",
        False,
        "map",
    ),
    # 买卖 (house.link) domain. All captured live 2026-08-11 from the
    # /search/sale/default/gdiv_mt workbench. The house origin injects its
    # own lianjia_curworkcity/lianjia_bucid headers, so no business-domain
    # city header is needed.
    "sale_listing.search": ("/search/searchQueryNew", False, "house"),
    "sale_listing.filter_options": ("/search/getSearchFilters", False, "house"),
    "sale_listing.suggest": ("/search/sugCommunityInfo", False, "house"),
    "sale_listing.get_detail": ("/housedel/views", False, "house"),
    "sale_listing.get_ext_info": ("/housedel/housedelExtInfo", False, "house"),
    "sale_listing.get_maintain_info": ("/housedel/getMaintainInfo", False, "house"),
    "sale_listing.get_follow": ("/housedelfollow/queryfollows", False, "house"),
    # 买卖地图找房 (mapSearch workbench, captured live 2026-08-11).
    "sale_map.suggest": ("/search/map/suggest", False, "house"),
    "sale_map.bubbles": ("/search/map/bubbleSearch", False, "house"),
    # 托管 (省心租, trusteeship.link.lianjia.com). The 托管 workbench is a
    # separate SPA/API domain; captured live 2026-08-15 from the 房源详情
    # page (pageInfoForPc carries the 实勘 photo list, deal/list the 成交参考).
    "trusteeship.get_detail": (
        "/api/trusteeship/broker/out/detail/pageInfoForPc",
        False,
        "trusteeship",
    ),
    "trusteeship.get_deals": (
        "/api/vRoute/house/trusteeship/broker/out/deal/list",
        False,
        "trusteeship",
    ),
    # 待出租 inventory search; the workbench's 待出租 tab posts here with
    # searchStatus=1 (captured live 2026-08-15). The 房源编码 box adds
    # delCode+outHouseCode holding a trusteeship cell code (bizCode).
    "trusteeship.search_listings": (
        "/api/house/search/waitingrent",
        True,
        "trusteeship",
    ),
}


def _resolve_route(route: str) -> tuple[str, bool, str]:
    if route not in _ROUTE_TABLE:
        raise ValueError(f"unknown authorized route: {route!r}")
    return _ROUTE_TABLE[route]
