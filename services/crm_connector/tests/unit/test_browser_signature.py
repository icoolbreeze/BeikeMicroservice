from __future__ import annotations

import app.infrastructure.browser_signature as browser_signature
from app.infrastructure.browser_signature import (
    detect_workbench_user_agent,
    _major_version,
)
from app.infrastructure.settings import (
    DEFAULT_HTTP_USER_AGENT,
    load_settings,
)


def test_major_version_parses_full_version_string() -> None:
    assert _major_version("151.0.7922.109") == 151
    assert _major_version(" 138.0 \n") == 138
    assert _major_version("") is None
    assert _major_version("beta") is None
    assert _major_version("Chrome 151") is None


def test_detect_prefers_chrome_and_freezes_minor_version(
    monkeypatch,
) -> None:
    # Only the major version may appear in the UA (user-agent reduction);
    # a full BLBeacon version like 151.0.7922.109 must not leak into it.
    monkeypatch.setattr(
        browser_signature,
        "_registry_value",
        lambda hive, subkey, value_name: "151.0.7922.109"
        if subkey.endswith("Google\\Chrome\\BLBeacon")
        else None,
    )

    ua = detect_workbench_user_agent()

    assert ua is not None
    assert "Chrome/151.0.0.0" in ua
    assert "Edg/" not in ua
    assert ua.startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")


def test_detect_falls_back_to_edge_with_edg_token(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_signature,
        "_registry_value",
        lambda hive, subkey, value_name: "151.0.4129.86"
        if subkey.endswith("Microsoft\\Edge\\BLBeacon")
        else None,
    )

    ua = detect_workbench_user_agent()

    assert ua is not None
    assert "Chrome/151.0.0.0" in ua
    assert ua.endswith("Edg/151.0.0.0")


def test_detect_skips_garbage_versions(monkeypatch) -> None:
    seen: list[str] = []

    def fake_registry_value(hive: str, subkey: str, value_name: str) -> str | None:
        seen.append(subkey)
        return "corrupt" if subkey.endswith("Google\\Chrome\\BLBeacon") else None

    monkeypatch.setattr(browser_signature, "_registry_value", fake_registry_value)

    assert detect_workbench_user_agent() is None
    # The chrome key was read but rejected; the edge keys were probed too.
    assert any(subkey.endswith("Microsoft\\Edge\\BLBeacon") for subkey in seen)


def test_env_override_beats_detection(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.settings.detect_workbench_user_agent",
        lambda: "UA-detected",
    )
    monkeypatch.setenv("CC_HTTP_USER_AGENT", "UA-from-env")

    assert load_settings().http_user_agent == "UA-from-env"


def test_detection_beats_static_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.settings.detect_workbench_user_agent",
        lambda: "UA-detected",
    )
    monkeypatch.delenv("CC_HTTP_USER_AGENT", raising=False)

    assert load_settings().http_user_agent == "UA-detected"


def test_static_default_when_nothing_detected(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.settings.detect_workbench_user_agent",
        lambda: None,
    )
    monkeypatch.delenv("CC_HTTP_USER_AGENT", raising=False)

    assert load_settings().http_user_agent == DEFAULT_HTTP_USER_AGENT
