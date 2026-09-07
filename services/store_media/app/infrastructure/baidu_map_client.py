"""百度地图地点检索 + 静态图：取行政区 / BD-09 坐标 + 全市静态图。"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

PLACE_REGION_URL = "https://api.map.baidu.com/place/v3/region"
STATIC_MAP_URL = "https://api.map.baidu.com/staticimage/v2"
DEFAULT_CITY = "成都市"
DEFAULT_CITY_CENTER = (104.07, 30.67)   # BD-09,全市能看见绕城
DEFAULT_CITY_ZOOM = 10
DEFAULT_MAP_WIDTH = 512
DEFAULT_MAP_HEIGHT = 360
_SKIP_POI = ("地铁", "公交站", "停车场", "充电站", "号门", "北门", "南门", "东门", "西门", "大门")
_SUFFIX_RE = re.compile(r"(第?[一二三四五六七八九十0-9]+期|[A-Za-z]区|[东南西北]区)$")
_KEEP_DISTRICT_SUFFIX = frozenset({"郫都区", "新津区", "天府新区", "天府新区南区", "高新西"})
_ALIAS_DISTRICT = {
    "简阳市": "简阳",
    "崇州市": "崇州",
    "都江堰市": "都江堰",
    "邛崃市": "邛崃",
    "彭州市": "彭州",
    "大邑县": "大邑",
    "蒲江县": "蒲江",
    "金堂县": "金堂",
}


@dataclass(frozen=True)
class BaiduPlaceHit:
    name: str
    latitude: float
    longitude: float
    area: str
    district: str
    address: str
    uid: str


class BaiduMapError(RuntimeError):
    pass


class BaiduMapClient:
    def __init__(
        self,
        ak: str,
        *,
        city: str = DEFAULT_CITY,
        request_timeout: float = 15.0,
        retries: int = 3,
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not ak.strip():
            raise BaiduMapError("百度地图 AK 未配置")
        self._ak = ak.strip()
        self._city = city
        self._timeout = request_timeout
        self._retries = max(1, retries)
        self._opener = opener or urlopen
        self._sleeper = sleeper

    def locate_community(self, name: str) -> BaiduPlaceHit | None:
        query = (name or "").strip()
        if not query:
            return None
        payload = self._search(query)
        hit = pick_community_poi(query, payload)
        if hit is not None:
            return hit
        core = community_query_name(query)
        if core != query:
            payload = self._search(core)
            return pick_community_poi(query, payload)
        return None

    def _search(self, query: str) -> list[dict[str, Any]]:
        params = urlencode({
            "query": query,
            "region": self._city,
            "region_limit": "true",
            "output": "json",
            "ak": self._ak,
        })
        request = Request(f"{PLACE_REGION_URL}?{params}")
        last_error: Exception | None = None
        body: Any = None
        for attempt in range(self._retries):
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                last_error = None
                break
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning(
                    "baidu place search retry %s/%s class=%s",
                    attempt + 1, self._retries, exc.__class__.__name__,
                )
                self._sleeper(0.8 * (attempt + 1))
        if last_error is not None:
            raise BaiduMapError(f"百度地点检索失败: {last_error.__class__.__name__}") from last_error
        if not isinstance(body, dict):
            raise BaiduMapError("百度地点检索返回了无法解析的响应")
        status = body.get("status")
        if status not in (0, "0"):
            raise BaiduMapError(f"百度地点检索 status={status}")
        results = body.get("results")
        return results if isinstance(results, list) else []


def community_query_name(name: str) -> str:
    text = re.sub(r"[（(].*?[)）]$", "", (name or "").strip()).strip()
    for _ in range(3):
        nxt = _SUFFIX_RE.sub("", text).strip("-· ")
        if nxt == text or len(nxt) < 2:
            break
        text = nxt
    return text


def normalize_chengdu_district(area: str) -> str:
    name = (area or "").strip()
    if not name:
        return ""
    name = _ALIAS_DISTRICT.get(name, name)
    if name in _KEEP_DISTRICT_SUFFIX:
        return name
    if name == "高新区":
        return "高新"
    if name.endswith("区") and name not in _KEEP_DISTRICT_SUFFIX:
        return name[:-1]
    return name


def pick_community_poi(community_name: str, results: list[Any]) -> BaiduPlaceHit | None:
    scored: list[tuple[int, BaiduPlaceHit]] = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        poi_name = str(raw.get("name") or "").strip()
        area = str(raw.get("area") or "").strip()
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        try:
            lat = float(location.get("lat"))
            lng = float(location.get("lng"))
        except (TypeError, ValueError):
            continue
        score = _poi_score(community_name, poi_name)
        if score <= 0 or not area:
            continue
        district = normalize_chengdu_district(area)
        if not district:
            continue
        scored.append((score, BaiduPlaceHit(
            name=poi_name,
            latitude=lat,
            longitude=lng,
            area=area,
            district=district,
            address=str(raw.get("address") or ""),
            uid=str(raw.get("uid") or ""),
        )))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _poi_score(community_name: str, poi_name: str) -> int:
    if not poi_name:
        return 0
    if any(token in poi_name for token in _SKIP_POI):
        return 0
    core = community_query_name(community_name)
    compact_poi = poi_name.replace("-", "").replace("·", "")
    compact_core = core.replace("-", "").replace("·", "")
    if poi_name == community_name or poi_name == core:
        return 100
    if compact_core and compact_core in compact_poi:
        return 80 + min(len(compact_core), 15)
    if community_name and community_name in poi_name:
        return 70
    return 0


# ---------------------------------------------------------------- static map


def static_city_map_url(
    ak: str,
    lng: float,
    lat: float,
    *,
    width: int = DEFAULT_MAP_WIDTH,
    height: int = DEFAULT_MAP_HEIGHT,
    zoom: int = DEFAULT_CITY_ZOOM,
    center_lng: float = DEFAULT_CITY_CENTER[0],
    center_lat: float = DEFAULT_CITY_CENTER[1],
    marker_color: str = "0xE74C3C",
) -> str:
    """百度静态图 URL：成都全市视角 + 单红点 marker。

    复用 `SM_BAIDU_MAP_AK`,但 share 路径不强制:AK 留空时也会返回带空
    ak 的 URL(便于测试不传 AK 的占位分支——路由层应该自己提前判断
    AK 是否配齐,这里只负责拼 URL)。
    """
    params = urlencode({
        "ak": (ak or "").strip(),
        "width": int(width),
        "height": int(height),
        "zoom": int(zoom),
        "center": f"{center_lng},{center_lat}",
        "markers": f"{lng},{lat}",
        "markerStyles": f"l,A,{marker_color}",
    })
    return f"{STATIC_MAP_URL}?{params}"


def fetch_static_city_map(
    ak: str,
    lng: float,
    lat: float,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 8.0,
    width: int = DEFAULT_MAP_WIDTH,
    height: int = DEFAULT_MAP_HEIGHT,
    zoom: int = DEFAULT_CITY_ZOOM,
) -> bytes:
    """下载百度静态图 PNG bytes。AK 缺失或网络错误抛 `BaiduMapError`。
    测试用 `opener` 注入,不走真实网络。
    """
    if not (ak or "").strip():
        raise BaiduMapError("百度地图 AK 未配置")
    url = static_city_map_url(ak, lng, lat, width=width, height=height, zoom=zoom)
    request = Request(url, headers={"User-Agent": "store-media-share/1.0"})
    open_fn = opener or urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise BaiduMapError(f"百度静态图失败: {exc.__class__.__name__}") from exc
