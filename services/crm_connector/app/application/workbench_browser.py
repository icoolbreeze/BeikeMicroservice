"""Headed Chromium that injects the stored CRM cookies and opens workbench pages.

The public ke.com listing URL requires a consumer login. The rental workbench
detail page (``lease-pz.link.lianjia.com/rent/house/detail/{id}``) is covered
by the connector's scanned session, which is the same injection used by
``run/open_browser.py``.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.domain.errors import (
    AuthenticationRequiredError,
    ConnectorError,
    UpstreamInvalidInputError,
    UpstreamNotConfiguredError,
)
from app.domain.models import ConnectionState
from app.infrastructure.kecom_session_provider import _decode_material
from app.infrastructure.settings import Settings

logger = logging.getLogger(__name__)

LISTING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,31}$")
WORKBENCH_ORIGIN = "https://lease-pz.link.lianjia.com"
COOKIE_DOMAINS = (".link.lianjia.com", ".ke.com", ".lianjia.com")
MAX_OPEN_PAGES = 8
GOTO_TIMEOUT_MS = 30_000

Navigator = Callable[[str, list[dict[str, Any]]], str]


def normalize_listing_id(listing_id: str) -> str:
    value = (listing_id or "").strip()
    if not LISTING_ID_RE.fullmatch(value):
        raise UpstreamInvalidInputError("listing_id is not a valid CRM house code")
    return value


def rental_detail_url(listing_id: str) -> str:
    return f"{WORKBENCH_ORIGIN}/rent/house/detail/{normalize_listing_id(listing_id)}"


def looks_like_login_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host == "login.ke.com" or host.endswith(".login.ke.com"):
        return True
    if host.endswith("lianjia.com") and path.startswith("/login"):
        return True
    if host.endswith("ke.com") and path.startswith("/login"):
        return True
    return False


def playwright_cookies(raw: Mapping[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        }
        for name, value in raw.items()
        for domain in COOKIE_DOMAINS
    ]


class WorkbenchBrowser:
    """Reuse one headed Chromium; refresh cookies from the DPAPI store on each open."""

    def __init__(
        self,
        settings: Settings,
        credential_store: Any | None,
        *,
        session_provider: Any | None = None,
        navigator: Navigator | None = None,
        state_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._store = credential_store
        self._session_provider = session_provider
        self._navigator = navigator
        self._state_path = state_path or (
            Path(settings.credential_store_path).expanduser().resolve().parent
            / "browser_state.json"
        )
        self._lock = threading.RLock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    def close(self) -> None:
        with self._lock:
            self._reset()

    def open_rental_listing(self, listing_id: str) -> str:
        url = rental_detail_url(listing_id)
        self._touch_session()
        cookies = self._load_cookies()
        injected = playwright_cookies(cookies)
        if self._navigator is not None:
            final = self._navigator(url, injected)
            self._reject_login(final, page=None)
            return final
        return self._open_with_playwright(url, injected)

    def _load_cookies(self) -> dict[str, str]:
        if self._store is None:
            raise UpstreamNotConfiguredError(
                "workbench open requires a configured CRM credential store"
            )
        active = self._store.load_active()
        if active is None:
            raise AuthenticationRequiredError("No active CRM credential")
        cookies = _decode_material(active.credential_material)
        if not cookies:
            raise AuthenticationRequiredError("Active CRM credential has no cookies")
        return cookies

    def _touch_session(self) -> None:
        """Best-effort TGC renewal so the headed browser gets fresh cookies."""
        provider = self._session_provider
        if provider is None or not hasattr(provider, "run_keepalive"):
            return
        try:
            status = provider.run_keepalive()
        except AuthenticationRequiredError:
            raise
        except Exception:
            logger.debug("workbench keepalive skipped", exc_info=True)
            return
        state = getattr(status, "state", None)
        if state is ConnectionState.AUTH_REQUIRED:
            raise AuthenticationRequiredError(
                "CRM authorization expired; refresh failed, rescan required"
            )

    def _open_with_playwright(self, url: str, injected: list[dict[str, Any]]) -> str:
        with self._lock:
            try:
                self._ensure_browser()
                assert self._context is not None
                self._context.add_cookies(injected)
                page = self._existing_page(url)
                if page is None:
                    page = self._context.new_page()
                    self._harden(page)
                    self._trim_pages(keep=page)
                page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                final = page.url
                self._save_state()
                self._reject_login(final, page)
                logger.info("workbench opened url_host=%s", urlparse(final).hostname)
                return final
            except (AuthenticationRequiredError, UpstreamInvalidInputError, ConnectorError):
                raise
            except Exception as exc:
                logger.exception("workbench open failed")
                self._reset()
                raise ConnectorError(f"failed to open workbench: {exc}") from exc

    def _reject_login(self, url: str, page: Any | None) -> None:
        if not looks_like_login_url(url):
            return
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        raise AuthenticationRequiredError(
            "CRM session expired; workbench redirected to login"
        )

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return
            except Exception:
                pass
            self._reset()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConnectorError(
                "Playwright is not installed; cannot open the workbench browser"
            ) from exc
        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(
                headless=False,
                args=["--disable-features=Translate", "--no-first-run"],
            )
            storage_state = (
                str(self._state_path) if self._state_path.exists() else None
            )
            try:
                context = browser.new_context(
                    no_viewport=True,
                    storage_state=storage_state,
                )
            except Exception:
                context = browser.new_context(no_viewport=True)
            context.on("page", self._harden)
        except Exception:
            try:
                playwright.stop()
            except Exception:
                pass
            raise
        self._playwright = playwright
        self._browser = browser
        self._context = context

    def _harden(self, page: Any) -> None:
        try:
            cdp = self._context.new_cdp_session(page)
            cdp.send("Debugger.disable")
            cdp.send("Debugger.setSkipAllPauses", {"skip": True})
        except Exception as exc:
            logger.debug("workbench debugger skip failed: %s", exc)
        page.on("dialog", lambda dialog: dialog.accept())

    def _existing_page(self, url: str) -> Any | None:
        if self._context is None:
            return None
        suffix = urlparse(url).path.rstrip("/")
        for page in list(self._context.pages):
            try:
                if page.is_closed():
                    continue
                current = urlparse(page.url).path.rstrip("/")
            except Exception:
                continue
            if current == suffix:
                return page
        return None

    def _trim_pages(self, keep: Any) -> None:
        if self._context is None:
            return
        live = []
        for page in list(self._context.pages):
            try:
                if page.is_closed():
                    continue
            except Exception:
                continue
            live.append(page)
        overflow = len(live) - MAX_OPEN_PAGES
        if overflow <= 0:
            return
        for page in live:
            if overflow <= 0:
                break
            if page is keep:
                continue
            try:
                page.close()
            except Exception:
                pass
            overflow -= 1

    def _save_state(self) -> None:
        if self._context is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(self._state_path))
        except Exception as exc:
            logger.debug("workbench save_state failed: %s", exc)

    def _reset(self) -> None:
        context, browser, playwright = self._context, self._browser, self._playwright
        self._context = None
        self._browser = None
        self._playwright = None
        for closer in (
            lambda: context.close() if context is not None else None,
            lambda: browser.close() if browser is not None else None,
            lambda: playwright.stop() if playwright is not None else None,
        ):
            try:
                closer()
            except Exception:
                pass
