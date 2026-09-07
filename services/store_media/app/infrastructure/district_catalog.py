"""把 CRM 筛选目录里的商圈归到行政区，供核分页筛选。

租赁搜索行只有 `bizCircleName`，没有区名。`districtId` 选项树是
「区 → 字母分组 → 商圈」，一份目录就能反查。少数商圈会挂在相邻多个区下，
这类房源筛选时每个相关区都能看到。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

UNKNOWN_DISTRICT = "未知"
STATUS_UNIQUE = "unique"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNKNOWN = "unknown"
_SKIP_NAMES = frozenset({"", "不限"})
FILTER_OPTIONS_PATH = "/api/v1/listings/rental/filter-options"


@dataclass(frozen=True)
class DistrictCatalog:
    districts: tuple[str, ...]
    bizcircle_to_districts: dict[str, tuple[str, ...]]

    def districts_for(self, bizcircle: str | None) -> tuple[str, ...]:
        name = (bizcircle or "").strip()
        if not name:
            return ()
        return self.bizcircle_to_districts.get(name, ())

    def label(self, bizcircle: str | None) -> str | None:
        names = self.districts_for(bizcircle)
        if not names:
            return None
        return "/".join(names)

    def matches(self, bizcircle: str | None, district: str | None) -> bool:
        wanted = (district or "").strip()
        if not wanted:
            return True
        names = self.districts_for(bizcircle)
        if wanted == UNKNOWN_DISTRICT:
            return not names
        return wanted in names

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> "DistrictCatalog":
        mapping: dict[str, list[str]] = {}
        districts: list[str] = []
        seen: set[str] = set()
        for bizcircle, district in pairs:
            biz = (bizcircle or "").strip()
            dist = (district or "").strip()
            if not biz or not dist:
                continue
            if dist not in seen:
                seen.add(dist)
                districts.append(dist)
            bucket = mapping.setdefault(biz, [])
            if dist not in bucket:
                bucket.append(dist)
        return cls(
            districts=tuple(districts),
            bizcircle_to_districts={name: tuple(values) for name, values in mapping.items()},
        )

    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (bizcircle, district)
            for bizcircle, names in self.bizcircle_to_districts.items()
            for district in names
        )


def classify_bizcircle(
    catalog: DistrictCatalog, bizcircle: str | None
) -> tuple[str | None, tuple[str, ...], str]:
    names = catalog.districts_for(bizcircle)
    if not names:
        return None, (), STATUS_UNKNOWN
    if len(names) == 1:
        return names[0], names, STATUS_UNIQUE
    return None, names, STATUS_AMBIGUOUS


def parse_district_catalog(options: list[Any] | tuple[Any, ...]) -> DistrictCatalog:
    root = _district_root(options)
    mapping: dict[str, list[str]] = {}
    districts: list[str] = []
    for district in _children(root):
        district_name = _clean_name(district.get("name") if isinstance(district, dict) else None)
        if district_name is None:
            continue
        districts.append(district_name)
        for bizcircle in _walk_bizcircles(district):
            bucket = mapping.setdefault(bizcircle, [])
            if district_name not in bucket:
                bucket.append(district_name)
    return DistrictCatalog(
        districts=tuple(districts),
        bizcircle_to_districts={name: tuple(values) for name, values in mapping.items()},
    )


def empty_district_catalog() -> DistrictCatalog:
    return DistrictCatalog(districts=(), bizcircle_to_districts={})


class DistrictCatalogFetcher:
    def __init__(
        self,
        crm_base_url: str,
        *,
        cache_seconds: int = 3600,
        request_timeout: float = 15.0,
        user_agent: str = "store-media-district-catalog/1.0",
    ) -> None:
        self._base_url = crm_base_url.rstrip("/")
        self._cache_seconds = cache_seconds
        self._timeout = request_timeout
        self._user_agent = user_agent
        self._lock = threading.Lock()
        self._catalog = empty_district_catalog()
        self._fetched_at = 0.0
        self._has_fetch = False

    def latest(self) -> DistrictCatalog:
        with self._lock:
            if (
                self._has_fetch
                and time.monotonic() - self._fetched_at < self._cache_seconds
            ):
                return self._catalog
        fetched = self._fetch()
        with self._lock:
            if fetched is not None:
                self._catalog = fetched
                self._fetched_at = time.monotonic()
                self._has_fetch = True
            return self._catalog

    def _fetch(self) -> DistrictCatalog | None:
        request = Request(
            f"{self._base_url}{FILTER_OPTIONS_PATH}",
            headers={"User-Agent": self._user_agent},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("district catalog fetch failed: %s", exc.__class__.__name__)
            return None
        if not isinstance(payload, list):
            logger.warning("district catalog is not a list")
            return None
        return parse_district_catalog(payload)


def _district_root(options: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    for option in options:
        if isinstance(option, dict) and option.get("key") == "districtId":
            return option
    return {}


def _walk_bizcircles(node: Any) -> list[str]:
    found: list[str] = []
    for child in _children(node):
        if not isinstance(child, dict):
            continue
        name = _clean_name(child.get("name"))
        value = child.get("value")
        if name is not None and value not in (None, ""):
            found.append(name)
        found.extend(_walk_bizcircles(child))
    return found


def _children(node: Any) -> list[Any]:
    if not isinstance(node, dict):
        return []
    children = node.get("children")
    return children if isinstance(children, list) else []


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if name in _SKIP_NAMES:
        return None
    if len(name) == 1 and name.isascii() and name.isalpha():
        return None
    return name
