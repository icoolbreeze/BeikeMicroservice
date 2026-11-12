"""Sensitive-field stripper for the local 买卖 archive.

Defence in depth alongside :mod:`rental_archive_sanitize`: the archive never
requests exact floor/address reveals, and every payload passes through this
module on the way to SQLite. The 买卖 payload additionally carries 业主预期价
/原购价格 fields and 维护信息 name-value rows, and sale follow
``creator_name`` embeds the agent's phone number, all of which are stripped
here. Coarse floor text (``floor_desc`` / ``floor_type`` / ``display_floor``,
i.e. 低/中/高) is deliberately kept — the list page already serves it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.infrastructure.rental_archive_sanitize import (
    as_archive_dict,
    sanitize_archive_payload,
)

__all__ = ["sanitize_sale_archive_payload", "as_archive_dict", "is_sensitive_key"]

# Extra lowercased key names dropped from 买卖 payloads, on top of the rental
# set that the first pass already handles.
SALE_SENSITIVE_KEYS = frozenset({
    "owner_reserve_price", "ownerreserveprice",
    "purchase_price", "purchaseprice",
})

# Substrings matched (case-insensitively for ASCII) against a name-value row's
# ``name`` or ``key``: a matching row is dropped wholesale.
SENSITIVE_ROW_LABELS = (
    "电话", "手机", "证件", "身份证",
    "业主预期", "原购价格", "具体楼层", "详细地址",
)

# Exact values of a row's ``key`` field (lowercased) that always drop the row.
SENSITIVE_ROW_KEYS = frozenset({
    "ownerphone", "ownermobile", "mobile", "telephone",
    "credential", "idcard", "idno",
})

_ROW_NAME_KEYS = ("name", "key")

# Mainland mobile number, embedded in strings such as follow creator_name.
_MOBILE_RE = re.compile(r"1[3-9]\d{9}")


def sanitize_sale_archive_payload(value: Any) -> Any:
    """Rental pass first (its key/row set), then the 买卖 pass with mobile
    redaction."""
    return _sale_pass(sanitize_archive_payload(value))


def is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.strip().lower()
    return lowered in SALE_SENSITIVE_KEYS


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact(item)
            for key, item in value.items()
            if not is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        cleaned = [
            _redact(item) for item in value if not _is_sensitive_row(item)
        ]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    if isinstance(value, str):
        return _MOBILE_RE.sub("", value)
    return value


# Alias kept for readability at call sites.
_sale_pass = _redact


def _is_sensitive_row(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return False
    for field_name in _ROW_NAME_KEYS:
        label = item.get(field_name)
        if not isinstance(label, str):
            continue
        lowered = label.strip().lower()
        if lowered in SENSITIVE_ROW_KEYS:
            return True
        for sensitive in SENSITIVE_ROW_LABELS:
            if sensitive in label:
                return True
    return False
