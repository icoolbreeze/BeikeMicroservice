"""Sensitive-field stripper for the local rental archive.

The workbench serves 具体楼层 (exact floor number) and 详细地址
(building/unit/room) behind a click-to-view, quota-limited reveal, so the
archive never requests them and the connector allowlist exposes no reveal
route. This module is the second line of defence: every payload passes
through it on the way to SQLite, so a future upstream shape change cannot
smuggle a sensitive field into local storage.

Coarse floor data (``floor_desc`` / ``total_floors`` / ``floor_type`` /
``total_floor``, i.e. 高/中/低) is deliberately *not* stripped — the cheap
list page already returns it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

# Lowercased key names dropped from every archived payload. Follow data is
# deliberately kept: the rental archive fetches 普租 detailFollow with the
# house_info call and stores the records (``follows`` / ``follow_total`` /
# ``follow_last_7d`` / follow authors are all allowed).
SENSITIVE_KEYS = frozenset({
    "signalfloor", "signal_floor", "具体楼层", "详细地址",
    "address", "houseaddress", "house_address",
    "owner_lowest_price", "ownerlowestprice",
    "phone", "manager_phone", "managerphone",
    "upload_user", "uploaduser",
})

# Labels of the name/value rows the 维护信息 / 房源信息 modules render. A row
# carrying one of these names IS the sensitive value, so the whole row is
# dropped rather than just a key.
SENSITIVE_FIELD_NAMES = frozenset({"具体楼层", "详细地址"})

_FIELD_NAME_KEYS = ("name", "field_name", "fieldName")


def sanitize_archive_payload(value: Any) -> Any:
    """Recursively drop quota-sensitive keys and field rows from ``value``."""
    if isinstance(value, Mapping):
        return {
            key: sanitize_archive_payload(item)
            for key, item in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        cleaned = [
            sanitize_archive_payload(item)
            for item in value
            if not _is_sensitive_field_row(item)
        ]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    return value


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key.strip().lower() in SENSITIVE_KEYS


def as_archive_dict(value: Any) -> Any:
    """Convert domain dataclasses into JSON-ready primitives.

    Archive-only helper: ``asdict`` keeps nested dataclasses but leaves
    datetimes as objects, which ``json.dumps`` cannot serialize.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return as_archive_dict(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): as_archive_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_archive_dict(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _is_sensitive_field_row(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    for key in _FIELD_NAME_KEYS:
        name = item.get(key)
        if isinstance(name, str) and name.strip() in SENSITIVE_FIELD_NAMES:
            return True
    return False
