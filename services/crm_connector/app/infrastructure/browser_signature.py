"""Reconstruct the workbench browser's User-Agent from the installed browser.

``chrome://version`` is internal to the browser and cannot be fetched from
the outside, but everything it shows that matters here — the browser
version — is available from the OS: Chrome (and Edge) write their current
version to the ``BLBeacon`` registry key on every update. Under the
user-agent reduction policy the UA string freezes the minor version, so
``Chrome/{major}.0.0.0`` is exactly what the real browser sends regardless
of the full version number. Reading the major version is therefore enough
to mirror the employee's actual browser signature.

Detection prefers Chrome (the workbench browser), then Edge, and fails
closed (``None``) so callers fall back to the static default on
non-Windows or registry-less machines.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# User-agent reduction: only the major version appears in the UA; the OS
# token is frozen at "Windows NT 10.0" for both Windows 10 and 11.
_CHROME_UA_TEMPLATE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
_EDGE_UA_TEMPLATE = _CHROME_UA_TEMPLATE + " Edg/{major}.0.0.0"

# (hive, key) pairs probed in order. BLBeacon is per-user (HKCU) but a
# machine-wide policy install may mirror it under HKLM.
_CHROME_VERSION_KEYS = (
    ("HKCU", r"Software\Google\Chrome\BLBeacon"),
    ("HKLM", r"SOFTWARE\Google\Chrome\BLBeacon"),
)
_EDGE_VERSION_KEYS = (
    ("HKCU", r"Software\Microsoft\Edge\BLBeacon"),
    ("HKLM", r"SOFTWARE\Microsoft\Edge\BLBeacon"),
)


def detect_workbench_user_agent() -> str | None:
    """Return the UA of the installed workbench browser, or None.

    Chrome is preferred (the CRM workbench targets desktop Chrome); Edge is
    the fallback so a VM that only ships Edge still mirrors its real
    Chromium signature, including the ``Edg`` token Edge appends.
    """
    for keys, template in (
        (_CHROME_VERSION_KEYS, _CHROME_UA_TEMPLATE),
        (_EDGE_VERSION_KEYS, _EDGE_UA_TEMPLATE),
    ):
        version = _first_registry_version(keys)
        if version is not None:
            ua = template.format(major=version)
            logger.info(
                "browser_signature.detected family=%s major=%s",
                "edge" if template is _EDGE_UA_TEMPLATE else "chrome",
                version,
            )
            return ua
    logger.info("browser_signature.not_found using static default")
    return None


def _first_registry_version(keys: Sequence[tuple[str, str]]) -> int | None:
    for hive, subkey in keys:
        version = _registry_value(hive, subkey, "version")
        if version is None:
            continue
        major = _major_version(version)
        if major is not None:
            return major
    return None


def _major_version(version: str) -> int | None:
    major = version.strip().split(".", 1)[0]
    try:
        return int(major)
    except ValueError:
        return None


def _registry_value(hive: str, subkey: str, value_name: str) -> str | None:
    if hive == "HKCU":
        hive_const = 0x80000001  # HKEY_CURRENT_USER
    else:
        hive_const = 0x80000002  # HKEY_LOCAL_MACHINE
    try:
        import winreg
    except ImportError:  # non-Windows (CI): no registry to read
        return None
    try:
        with winreg.OpenKey(hive_const, subkey) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
    except OSError:
        return None
    return value if isinstance(value, str) else None
