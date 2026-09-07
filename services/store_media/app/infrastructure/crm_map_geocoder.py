"""贝壳买卖地图联想（BD-09）:小区名 → 行政区 + 坐标。"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.infrastructure.baidu_map_client import BaiduPlaceHit, pick_community_poi

SUGGEST_PATH = "/api/v1/listings/sale/map/suggest"


class CrmMapGeocoder:
    def __init__(
        self,
        crm_base_url: str,
        *,
        request_timeout: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._base = crm_base_url.rstrip("/")
        self._timeout = request_timeout
        self._opener = opener or urlopen

    def locate_community(self, name: str, community_id: str | None = None) -> BaiduPlaceHit | None:
        query = (name or "").strip()
        if not query:
            return None
        request = Request(f"{self._base}{SUGGEST_PATH}?query={quote(query)}")
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list):
            return None
        wanted = str(community_id or "").strip()
        results = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("item_type") not in (None, "", "community"):
                continue
            poi = {
                "name": item.get("text") or item.get("alias") or "",
                "area": item.get("district_name") or "",
                "address": item.get("bizcircle_name") or "",
                "uid": str(item.get("suggestion_id") or ""),
                "location": {
                    "lat": item.get("latitude"),
                    "lng": item.get("longitude"),
                },
            }
            if wanted and poi["uid"] == wanted:
                return pick_community_poi(str(poi["name"]), [poi])
            results.append(poi)
        return pick_community_poi(query, results)
