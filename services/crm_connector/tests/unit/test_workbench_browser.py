from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.workbench_browser import (
    COOKIE_DOMAINS,
    WorkbenchBrowser,
    looks_like_login_url,
    playwright_cookies,
    rental_detail_url,
)
from app.domain.errors import (
    AuthenticationRequiredError,
    UpstreamInvalidInputError,
    UpstreamNotConfiguredError,
)
from app.domain.models import ConnectionState, ProviderStatus
from app.domain.providers.credential_store import ActiveCredential
from app.infrastructure.settings import Settings


class FakeStore:
    def __init__(self, credential: ActiveCredential | None) -> None:
        self._credential = credential

    def load_active(self) -> ActiveCredential | None:
        return self._credential


def _credential(material: bytes = b'{"UCID":"1","puzu_lease_token":"tok"}') -> ActiveCredential:
    return ActiveCredential(
        session_id="sess-1",
        employee_principal="100000003",
        credential_material=material,
        expires_at=datetime(2026, 9, 1, tzinfo=UTC),
        credential_version=1,
    )


def test_rental_detail_url_is_workbench_not_kecom() -> None:
    assert rental_detail_url("106106022223") == (
        "https://lease-pz.link.lianjia.com/rent/house/detail/106106022223"
    )


@pytest.mark.parametrize(
    "listing_id",
    ["", "ab", "../etc", "https://evil.test/x", "1061 062", "id/../../x"],
)
def test_rental_detail_url_rejects_junk(listing_id: str) -> None:
    with pytest.raises(UpstreamInvalidInputError):
        rental_detail_url(listing_id)


def test_playwright_cookies_cover_sso_domains() -> None:
    cookies = playwright_cookies({"UCID": "1", "puzu_lease_token": "tok"})
    names = {(item["name"], item["domain"]) for item in cookies}
    assert ("puzu_lease_token", ".link.lianjia.com") in names
    assert set(COOKIE_DOMAINS) <= {item["domain"] for item in cookies}


def test_looks_like_login_url() -> None:
    assert looks_like_login_url("https://login.ke.com/login")
    assert looks_like_login_url(
        "https://lease-pz.link.lianjia.com/login?gotoURL=%252F"
    )
    assert not looks_like_login_url(
        "https://lease-pz.link.lianjia.com/rent/house/detail/106106022223"
    )


def test_open_requires_configured_store() -> None:
    browser = WorkbenchBrowser(Settings(), None)
    with pytest.raises(UpstreamNotConfiguredError):
        browser.open_rental_listing("106106022223")


def test_open_requires_active_cookies() -> None:
    settings = Settings()
    missing = WorkbenchBrowser(settings, FakeStore(None))
    with pytest.raises(AuthenticationRequiredError):
        missing.open_rental_listing("106106022223")
    empty = WorkbenchBrowser(settings, FakeStore(_credential(b"{}")))
    with pytest.raises(AuthenticationRequiredError):
        empty.open_rental_listing("106106022223")


def test_open_uses_injected_navigator_and_workbench_url() -> None:
    seen: list[tuple[str, list[dict]]] = []

    def navigator(url: str, cookies: list[dict]) -> str:
        seen.append((url, cookies))
        return url

    browser = WorkbenchBrowser(
        Settings(), FakeStore(_credential()), navigator=navigator
    )
    opened = browser.open_rental_listing("106106022223")
    assert opened.endswith("/rent/house/detail/106106022223")
    assert seen[0][0] == opened
    assert any(item["name"] == "puzu_lease_token" for item in seen[0][1])


def test_open_keepalive_auth_required_does_not_navigate() -> None:
    seen: list[str] = []

    class DeadSession:
        def run_keepalive(self) -> ProviderStatus:
            return ProviderStatus(ConnectionState.AUTH_REQUIRED, "expired")

    def navigator(url: str, _cookies: list[dict]) -> str:
        seen.append(url)
        return url

    browser = WorkbenchBrowser(
        Settings(),
        FakeStore(_credential()),
        session_provider=DeadSession(),
        navigator=navigator,
    )
    with pytest.raises(AuthenticationRequiredError):
        browser.open_rental_listing("106106022223")
    assert seen == []


def test_open_treats_login_redirect_as_auth_required() -> None:
    def navigator(_url: str, _cookies: list[dict]) -> str:
        return "https://login.ke.com/login"

    browser = WorkbenchBrowser(
        Settings(), FakeStore(_credential()), navigator=navigator
    )
    with pytest.raises(AuthenticationRequiredError):
        browser.open_rental_listing("106106022223")
