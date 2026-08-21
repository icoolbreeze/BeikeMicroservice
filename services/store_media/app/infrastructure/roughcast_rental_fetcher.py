"""Fixed, mobile-friendly feed for rental roughcast listings.

The browser never talks to the CRM directly.  This small adapter calls the
existing CRM connector with the one product query that this MVP supports:
unrestricted rental inventory with the CRM's ``fitment=002`` (毛坯) filter.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app.infrastructure.featured_fetcher import public_image_url


ROUGHCAST_SCOPE = "all"
ROUGHCAST_FITMENT_CODE = "002"
ROUGHCAST_PAGE_SIZE = 30
ROUGHCAST_MAX_PAGE = 1000
ROUGHCAST_FLOOR_DETAIL_WORKERS = 6
_LISTING_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class RoughcastRentalListing:
    """Only the fields that the mobile list is allowed to expose."""

    listing_id: str | None
    community: str
    layout: str
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str
    floor: str
    image: str | None


@dataclass(frozen=True)
class RoughcastRentalFeed:
    items: tuple[RoughcastRentalListing, ...]
    updated_at: str
    page: int
    has_more: bool


@dataclass(frozen=True)
class RoughcastProspectPhoto:
    url: str
    label: str


@dataclass(frozen=True)
class RoughcastProspectGallery:
    photos: tuple[RoughcastProspectPhoto, ...]


class RoughcastRentalFetcher:
    """Retrieve and cache the fixed rental + 毛坯 CRM query.

    There are deliberately no public filter arguments.  Rental is selected by
    the connector endpoint and roughcast is the sole CRM condition filter.
    """

    def __init__(
        self,
        crm_base_url: str,
        *,
        cache_seconds: int = 60,
        request_timeout: float = 15.0,
        user_agent: str = "store-media-roughcast/1.0",
    ) -> None:
        self._base_url = crm_base_url.rstrip("/")
        self._cache_seconds = cache_seconds
        self._timeout = request_timeout
        self._user_agent = user_agent
        self._feeds: dict[int, RoughcastRentalFeed] = {}
        self._feed_fetched_at: dict[int, float] = {}
        self._galleries: dict[str, RoughcastProspectGallery] = {}
        self._gallery_fetched_at: dict[str, float] = {}
        self._known_listing_ids: set[str] = set()
        self._lock = threading.Lock()

    def latest(self, page: int = 1) -> RoughcastRentalFeed | None:
        """Return one fixed-filter page, or its last successful cached value."""
        if page < 1 or page > ROUGHCAST_MAX_PAGE:
            return None
        with self._lock:
            cached = self._feeds.get(page)
            fetched_at = self._feed_fetched_at.get(page, 0.0)
            if cached is not None and time.monotonic() - fetched_at < self._cache_seconds:
                return cached

        fetched = self._fetch(page)
        if fetched is not None:
            with self._lock:
                self._feeds[page] = fetched
                self._feed_fetched_at[page] = time.monotonic()
                self._known_listing_ids.update(
                    item.listing_id for item in fetched.items if item.listing_id
                )
            return fetched

        with self._lock:
            return self._feeds.get(page)

    def knows_listing(self, listing_id: str) -> bool:
        safe_id = _safe_listing_id(listing_id)
        with self._lock:
            return safe_id is not None and safe_id in self._known_listing_ids

    def prospect(self, listing_id: str) -> RoughcastProspectGallery | None:
        """Return only sanitized REAL prospect photos for an already-seen row."""
        safe_id = _safe_listing_id(listing_id)
        if safe_id is None:
            return None
        with self._lock:
            if safe_id not in self._known_listing_ids:
                return None
            cached = self._galleries.get(safe_id)
            fetched_at = self._gallery_fetched_at.get(safe_id, 0.0)
            if cached is not None and time.monotonic() - fetched_at < self._cache_seconds:
                return cached

        fetched = self._fetch_prospect(safe_id)
        if fetched is not None:
            with self._lock:
                self._galleries[safe_id] = fetched
                self._gallery_fetched_at[safe_id] = time.monotonic()
            return fetched

        with self._lock:
            return self._galleries.get(safe_id)

    def _fetch(self, page: int) -> RoughcastRentalFeed | None:
        body = self._post_json(
            "/api/v1/listings/rental/search",
            {
                "scope": ROUGHCAST_SCOPE,
                "condition_filters": {"fitment": ROUGHCAST_FITMENT_CODE},
                "page": page,
                "page_size": ROUGHCAST_PAGE_SIZE,
            },
        )
        if body is None:
            return None

        rows = body.get("items")
        if not isinstance(rows, list):
            return None

        display_rows = self._fill_missing_floors([
            row for row in rows[:ROUGHCAST_PAGE_SIZE] if isinstance(row, dict)
        ])
        listings = tuple(
            listing
            for row in display_rows
            for listing in (_listing_from_row(row),)
            if listing is not None
        )
        return RoughcastRentalFeed(
            items=listings,
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            page=page,
            has_more=(
                body["has_more"]
                if isinstance(body.get("has_more"), bool)
                else len(rows) >= ROUGHCAST_PAGE_SIZE
            ),
        )

    def _fill_missing_floors(
        self, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Backfill floor data while an older connector process is still active.

        Updated connector versions expose floorLevel/totalFloor on search rows.
        The fallback is limited to normal-rental rows with missing floor data
        and disappears automatically once the updated process is restarted.
        """
        indexes = [
            index
            for index, row in enumerate(rows)
            if row.get("del_type") == 2
            and _safe_listing_id(row.get("listing_id")) is not None
            and (
                not _normalize_floor_desc(row.get("floor_desc"))
                or _number(row.get("total_floors")) is None
            )
        ]
        if not indexes:
            return rows

        def fetch_detail(index: int) -> tuple[int, dict[str, Any] | None]:
            listing_id = _safe_listing_id(rows[index].get("listing_id"))
            if listing_id is None:
                return index, None
            return index, self._get_json(
                f"/api/v1/listings/rental/{quote(listing_id, safe='')}"
            )

        enriched = list(rows)
        with ThreadPoolExecutor(
            max_workers=min(ROUGHCAST_FLOOR_DETAIL_WORKERS, len(indexes))
        ) as executor:
            for index, detail in executor.map(fetch_detail, indexes):
                if not isinstance(detail, dict):
                    continue
                merged = dict(enriched[index])
                if not _normalize_floor_desc(merged.get("floor_desc")):
                    merged["floor_desc"] = detail.get("floor_desc")
                if _number(merged.get("total_floors")) is None:
                    merged["total_floors"] = detail.get("total_floors")
                enriched[index] = merged
        return enriched

    def _fetch_prospect(self, listing_id: str) -> RoughcastProspectGallery | None:
        body = self._get_json(
            f"/api/v1/listings/rental/{quote(listing_id, safe='')}/prospect"
        )
        if body is None:
            return None
        rows = body.get("photos")
        if not isinstance(rows, list):
            return None

        photos: list[RoughcastProspectPhoto] = []
        seen_urls: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("image_type") != "REAL":
                continue
            image_url = _safe_public_image_url(row.get("url"))
            if image_url is None or image_url in seen_urls:
                continue
            seen_urls.add(image_url)
            photos.append(RoughcastProspectPhoto(
                url=image_url,
                label=_display_text(row.get("room_name"), fallback="实勘图片"),
            ))
        return RoughcastProspectGallery(photos=tuple(photos))

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": self._user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            return None
        return response_body if isinstance(response_body, dict) else None

    def _get_json(self, path: str) -> dict[str, Any] | None:
        request = Request(
            f"{self._base_url}{path}",
            headers={"Accept": "application/json", "User-Agent": self._user_agent},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            return None
        return response_body if isinstance(response_body, dict) else None


def _listing_from_row(row: dict[str, Any]) -> RoughcastRentalListing | None:
    community = _display_text(row.get("community"), fallback="")
    if not community:
        return None

    total_floors = _number(row.get("total_floors"))
    floor_desc = _normalize_floor_desc(row.get("floor_desc"))
    total_floor_text = (
        f"共{total_floors:.0f}层"
        if total_floors is not None and total_floors > 0
        else ""
    )
    floor = " · ".join(part for part in (floor_desc, total_floor_text) if part)
    if not floor:
        floor = "楼层待定"
    return RoughcastRentalListing(
        listing_id=_safe_listing_id(row.get("listing_id")),
        community=community,
        layout=_display_text(row.get("layout"), fallback="户型待定"),
        area_sqm=_number(row.get("area_sqm")),
        monthly_rent_yuan=_number(row.get("monthly_rent_yuan")),
        orientation=_display_text(row.get("orientation"), fallback="朝向待定"),
        floor=floor,
        image=_safe_public_image_url(row.get("title_image_url")),
    )


def _display_text(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def _safe_listing_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    listing_id = value.strip()
    return listing_id if _LISTING_ID_PATTERN.fullmatch(listing_id) else None


def _normalize_floor_desc(value: object) -> str:
    floor_desc = _display_text(value, fallback="")
    if floor_desc in {"高", "中", "低"}:
        return f"{floor_desc}楼层"
    return floor_desc


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_public_image_url(value: object) -> str | None:
    raw = value if isinstance(value, str) else None
    image = public_image_url(raw)
    if image is None:
        return None
    parsed = urlparse(image)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return image
