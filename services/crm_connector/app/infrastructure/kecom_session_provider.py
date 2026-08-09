from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Callable

import httpx

from app.domain.errors import AuthenticationRequiredError, NetworkRequiredError
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
)

# Account / whoami probe used by the keepalive timer to extend the
# lianjia_ssid sliding window.
_KEEPALIVE_ROUTE = "identity.me"
_KEEPALIVE_PATH = "/api/puzuHouse/puzu/house/auth/pc/accountRightInfo"

# Default business-domain code. The CRM API requires the matching header on
# every business call.
_DEFAULT_CITY_HEADER = "house_current_work_citycode"


class KecomSessionProvider:
    """Authentication boundary that owns the active business cookie jar.

    Wraps a ``CredentialStore`` plus the bootstrap adapter so that callers
    of ``authorized_fetch`` see a single, opaque "send a controlled request
    on my behalf" interface. The provider never exposes raw cookies,
    tokens or the bootstrap material.
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
            headers={"user-agent": "crm-connector/0.1"},
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
                self._deactivate_locked(active.session_id, "expired")
                return ProviderStatus(
                    ConnectionState.AUTH_REQUIRED,
                    "CRM authorization expired; refresh or rescan required",
                )

            state, message = self._derive_state_locked(active)
            return ProviderStatus(state, message)

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

        return UpstreamResponse(status_code=response.status_code, body=response.json())

    def bound_principal(self) -> Principal | None:
        """Return the employee principal this session was bootstrapped with.

        The upstream accountRightInfo envelope does not echo the principal,
        so the identity recorded at scan time (the ``UCID`` cookie) is the
        authoritative local source.
        """
        with self._lock:
            active = self._active or self._store.load_active()
            self._active = active
        if active is None or not active.employee_principal:
            return None
        return Principal(employee_principal=active.employee_principal)

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
                refreshed = self._make_credential(self._bootstrap.refresh(active))
                if refreshed is None:
                    self._deactivate_locked(active.session_id, "upstream_rejected")
                    self._degraded_message = "keepalive failed; employee rescan required"
                    return ProviderStatus(
                        ConnectionState.AUTH_REQUIRED,
                        self._degraded_message,
                    )
                self._store.save(refreshed)
                self._active = refreshed
                self._store.invalidate(active.session_id, "replaced")
                return ProviderStatus(
                    ConnectionState.READY,
                    "CRM authorization refreshed via TGC after keepalive failure",
                )
            state, message = self._derive_state_locked(active)
            return ProviderStatus(state, message)

    # -- internals: send + refresh -----------------------------------------

    def _send_authorized(
        self, request: AuthorizedRequest, active: ActiveCredential, request_id: str
    ) -> httpx.Response:
        cookies = _decode_material(active.credential_material)
        method = request.method.upper()
        path, requires_city_header, origin = _resolve_route(request.route)
        base_url = (
            self._settings.crm_map_origin
            if origin == "map"
            else self._settings.crm_business_origin
        )
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
        if requires_city_header:
            headers[_DEFAULT_CITY_HEADER] = self._settings.crm_default_city_code
        headers["x-request-id"] = request_id

        try:
            return self._client.request(
                method,
                url,
                params=params or None,
                json=body if body is not None else None,
                cookies=_select_cookies(cookies),
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
            self._deactivate_locked(active.session_id, "upstream_rejected")
            refreshed = self._make_credential(self._bootstrap.refresh(active))
            if refreshed is None:
                return None
            self._store.save(refreshed)
            self._active = refreshed
            self._store.invalidate(active.session_id, "replaced")
            return refreshed
        finally:
            self._refresh_lock.release()

    @staticmethod
    def _make_credential(result: BootstrapResult | None) -> ActiveCredential | None:
        if result is None:
            return None
        # active.session_id will be supplied by install_fresh_credential /
        # KecomSessionProvider when persisting; here we only need a unique
        # placeholder so the CredentialStore records a new row.
        return ActiveCredential(
            session_id=str(uuid.uuid4()),
            employee_principal="",
            credential_material=result.credential_material,
            expires_at=result.expires_at,
            credential_version=result.credential_version,
            refresh_material=result.refresh_material,
        )

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
            self._deactivate(active.session_id, "expired")
            raise AuthenticationRequiredError(
                "CRM authorization expired; refresh or rescan required"
            )
        return active

    def _deactivate(self, session_id: str, reason: str) -> None:
        with self._lock:
            self._deactivate_locked(session_id, reason)

    def _deactivate_locked(self, session_id: str, reason: str) -> None:
        self._store.invalidate(session_id, reason)  # type: ignore[arg-type]
        # The local _active slot may already have been replaced by save();
        # only clear it if it still matches the invalidated id.
        if self._active is not None and self._active.session_id == session_id:
            self._active = None
        if reason == "upstream_rejected":
            self._degraded_message = "upstream rejected credential; refresh attempted"

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
        if isinstance(msg, str) and ("未登录" in msg or "请先登录" in msg):
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
}


def _resolve_route(route: str) -> tuple[str, bool, str]:
    if route not in _ROUTE_TABLE:
        raise ValueError(f"unknown authorized route: {route!r}")
    return _ROUTE_TABLE[route]
