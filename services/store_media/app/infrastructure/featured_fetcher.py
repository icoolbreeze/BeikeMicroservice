"""精选房源大屏数据：从 crm_connector 拉取买卖/租赁房源并规范化为展示形状。

图片约定（2026-08-11 视觉核验 + docs/rental-image-cdn.md）：
- API 返回的 img.ljcdn.com URL 是原图地址，按路径分桶保护：
  - ``lease-image/house/`` 封面桶：**原图公开 200 且无水印**（唯一满足
    “原图无水印”的来源）；
  - ``110000-inspection/`` 实勘桶与 ``hdic-frame/`` 户型图桶：原图 403
    （任何人含登录态都无法访问）；尺寸后缀变体（.450x/.750x/.800x/.1500x.jpg）
    公开但**带水印**；
- **买卖（house.link）与租赁（lease-pz）同规律**：买卖 ``surfaceImage``
  永远在实勘桶（2026-08-11 全量 1590 条核验，含外观呈现/美化房/推广筛选），
  CDN 命令变体（``!m_fill,l_dy,w_600`` 等）同样带水印，ke.com 外网页有
  验证码——**买卖房源没有可公开获取的无水印原图**；
- 因此：
  - 轮播大图（``original_image``）只用 ``lease-image`` 桶原图 URL 直连，
    不拼任何尺寸后缀（拼了反而带水印）——当前数据下只有租赁托管房源满足；
  - 侧边列表缩略图（``image``）用公开变体（.1500x.jpg），保证买卖/租赁
    列表都有真实图片可展示；
- 无图片字段或图片不在可公开桶的房源直接丢弃。

采集策略（2026-08-11 实测）：
- 数据范围不限：买卖 scope=all；租赁也使用 scope=all（CRM 原生
  ``relationRange=0``，即“范围：不限”）；
- 优先查询并排序指定小区：双桥路南二街 / 双桥路南一街 / 成发紫东阳光 /
  成发紫悦府 / 双华苑 / 经华北路1号院（小区名 → sale_community_suggest 解析 id）；
- 某一业务类型少于 6 套可展示房源时，以双桥路南一街为圆心做 1 公里范围查询，
  把周边房源追加在指定小区之后；
- 图片展示区数量不设上限：能捞到多少带无水印原图的就展示多少，
  列表同样展示全部真实房源（含普租/托管、买卖/租赁）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_IMAGE_COMMAND_SUFFIX = re.compile(r"!.*$")
_SIZE_SUFFIX_RE = re.compile(r"\.\d{3,4}x\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)

_TAG_ICONS: dict[str, str] = {
    "新上": "fa-star",
    "急售": "fa-bolt",
    "必看": "fa-fire",
    "热销": "fa-fire",
    "独家": "fa-crown",
    "地铁": "fa-train-subway",
    "学区": "fa-graduation-cap",
    "VR": "fa-vr-cardboard",
    "满五": "fa-certificate",
    "电梯": "fa-elevator",
    "精装": "fa-paintbrush",
    "拎包": "fa-suitcase",
    "钥匙": "fa-key",
    "车位": "fa-car",
    "别墅": "fa-house",
    "免佣": "fa-hand-holding-dollar",
}

_TAG_TYPES: dict[str, str] = {
    "新上": "new",
    "急售": "urgent",
    "必看": "hot",
    "热销": "hot",
    "独家": "exclusive",
}

# 优先采集的小区（2026-08-11 通过 sale_community_suggest 解析出的成都成华 id）。
PRIORITY_COMMUNITIES: tuple[tuple[str, str], ...] = (
    ("双桥路南二街", "1611048035758"),
    ("双桥路南一街", "16000000145204"),
    ("成发紫东阳光", "1611063740147"),
    ("成发紫悦府", "1620035540190520"),
    ("双华苑", "3011053244694"),
    ("经华北路1号院", "3011052177352"),
)

# 每个范围最多翻多少页（防上游异常时无限循环）。
MAX_PAGES = 5

# 指定小区的有效房源少于这个数量时，向圆心周边扩展。该值只决定是否回退，
# 不限制最终列表或轮播展示数量。
NEARBY_EXPANSION_THRESHOLD = len(PRIORITY_COMMUNITIES)
NEARBY_CENTER = "双桥路南一街"
NEARBY_RADIUS_METERS = 1_000
_MAX_COMMUNITY_IDS_PER_REQUEST = 100
_RENTAL_SCOPES = ("all",)
_PRIORITY_RANK = {name: index for index, (name, _id) in enumerate(PRIORITY_COMMUNITIES)}


@dataclass(frozen=True)
class FeaturedTag:
    type: str
    icon: str
    text: str


@dataclass(frozen=True)
class FeaturedListing:
    id: str
    title: str
    layout: str
    area: str
    floor: str
    orient: str
    decor: str
    price: str
    priceUnit: str
    unitPrice: str
    location: str
    tags: list[FeaturedTag]
    image: str
    desc: str
    original_image: str | None = None
    """无水印原图 URL（仅 lease-image 桶原图）；无则 None。

    轮播（大图区）只展示 ``original_image`` 非空的房源；侧边列表缩略图
    可用 ``image``（可能是带水印的公开变体）。
    """

    def __hash__(self) -> int:
        return hash((self.id, self.title, self.price, self.priceUnit))


@dataclass(frozen=True)
class FeaturedFeed:
    sale: list[FeaturedListing] = field(default_factory=list)
    rent: list[FeaturedListing] = field(default_factory=list)
    sale_total: int | None = None
    rent_total: int | None = None
    updated_at: str = ""


class FeaturedSnapshotStore:
    """Read the last successful feed exported by the local CRM machine.

    The exporter replaces the JSON file atomically, so a request either sees
    the previous complete snapshot or the new complete snapshot. A malformed
    or half-written file never replaces the last good in-memory value.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._last_mtime_ns: int | None = None
        self._feed: FeaturedFeed | None = None

    def latest(self) -> FeaturedFeed | None:
        try:
            stat = self._path.stat()
        except OSError:
            return self._feed

        with self._lock:
            if self._last_mtime_ns == stat.st_mtime_ns:
                return self._feed
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                feed = _featured_feed_from_payload(payload)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return self._feed
            self._last_mtime_ns = stat.st_mtime_ns
            self._feed = feed
            return feed


def _featured_feed_from_payload(payload: object) -> FeaturedFeed:
    if not isinstance(payload, dict):
        raise ValueError("featured snapshot must be an object")

    def listing(raw: object) -> FeaturedListing:
        if not isinstance(raw, dict):
            raise ValueError("featured listing must be an object")
        tags = raw.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("featured listing tags must be a list")
        return FeaturedListing(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            layout=str(raw.get("layout") or ""),
            area=str(raw.get("area") or ""),
            floor=str(raw.get("floor") or ""),
            orient=str(raw.get("orient") or ""),
            decor=str(raw.get("decor") or ""),
            price=str(raw.get("price") or ""),
            priceUnit=str(raw.get("priceUnit") or ""),
            unitPrice=str(raw.get("unitPrice") or ""),
            location=str(raw.get("location") or ""),
            tags=[
                FeaturedTag(
                    type=str(tag.get("type") or "attr"),
                    icon=str(tag.get("icon") or "fa-tag"),
                    text=str(tag.get("text") or ""),
                )
                for tag in tags
                if isinstance(tag, dict)
            ],
            image=str(raw.get("image") or ""),
            desc=str(raw.get("desc") or ""),
            original_image=(
                str(raw["original_image"])
                if raw.get("original_image")
                else None
            ),
        )

    sale = payload.get("sale") or []
    rent = payload.get("rent") or []
    if not isinstance(sale, list) or not isinstance(rent, list):
        raise ValueError("featured snapshot sale/rent must be lists")
    return FeaturedFeed(
        sale=[listing(item) for item in sale],
        rent=[listing(item) for item in rent],
        sale_total=payload.get("sale_total"),
        rent_total=payload.get("rent_total"),
        updated_at=str(payload.get("updated_at") or ""),
    )


def original_image_url(raw: str | None) -> str | None:
    """只接受 ``lease-image`` 桶的原图 URL（公开 200 且无水印）。

    - 剥离 ``!`` 命令后缀（如 ``!m_fill,l_dy``）；
    - 若最终路径在 ``lease-image`` 桶且未带尺寸后缀，返回原图 URL
      （**不拼任何尺寸后缀**，尺寸变体反而带水印）；
    - 其余桶（inspection/frame）或带尺寸后缀的变体没有无水印原图，返回 None。
    """
    if not raw:
        return None
    url = raw.strip()
    if not url.startswith("http"):
        return None
    url = _IMAGE_COMMAND_SUFFIX.sub("", url).strip()
    if not url or not url.startswith("http"):
        return None
    if "/lease-image/" not in url:
        return None
    if _SIZE_SUFFIX_RE.search(url):
        return None
    return url


def public_image_url(raw: str | None) -> str | None:
    """把 API 原图 URL 归一化为可公开获取的尺寸变体（侧边列表缩略图用）。

    尺寸变体（.1500x.jpg 等）公开 200，但带平台水印——因此只用于列表小图，
    轮播大图必须走 ``original_image_url`` 的无水印原图。
    """
    if not raw:
        return None
    url = raw.strip()
    if not url.startswith("http"):
        return None
    url = _IMAGE_COMMAND_SUFFIX.sub("", url).strip()
    if not url or not url.startswith("http"):
        return None
    if "/lease-image/" in url and not _SIZE_SUFFIX_RE.search(url):
        return url
    if _SIZE_SUFFIX_RE.search(url):
        return url
    return url + ".1500x.jpg"


def _sale_tags(raw_tags: tuple[str, ...]) -> list[FeaturedTag]:
    tags: list[FeaturedTag] = []
    for text in raw_tags:
        if not text:
            continue
        icon = next((icon for key, icon in _TAG_ICONS.items() if key in text), "fa-tag")
        tag_type = next(
            (type_ for key, type_ in _TAG_TYPES.items() if key in text), "attr"
        )
        tags.append(FeaturedTag(type=tag_type, icon=icon, text=text))
        if len(tags) >= 3:
            break
    if not tags:
        tags.append(FeaturedTag(type="attr", icon="fa-house", text="在售房源"))
    return tags


def _rent_tags(del_type: int | None) -> list[FeaturedTag]:
    tags = [FeaturedTag(type="attr", icon="fa-suitcase", text="拎包入住")]
    if del_type == 5:
        tags.insert(0, FeaturedTag(type="hot", icon="fa-hand-holding-hand", text="托管直租"))
    else:
        tags.insert(0, FeaturedTag(type="attr", icon="fa-key", text="整租"))
    return tags


def _sale_layout(unit_type: str | None) -> str:
    if not unit_type:
        return "户型待定"
    parts = [part.strip() for part in str(unit_type).split("-") if part.strip()]
    if len(parts) >= 3:
        return f"{parts[0]}室{parts[1]}厅{parts[2]}卫"
    return unit_type


def _price_number(price_text: str | None, price_yuan: float | None) -> str:
    if price_text:
        match = re.search(r"[\d.]+", price_text)
        if match:
            return match.group(0)
    if price_yuan:
        return f"{price_yuan / 10_000:.0f}"
    return "0"


def _format_floor(floor_type: str | None, total_floor: int | None) -> str:
    if floor_type:
        if total_floor:
            return f"{floor_type}层/{total_floor}层"
        return f"{floor_type}楼层"
    if total_floor:
        return f"共{total_floor}层"
    return "楼层待定"


def _chunks(items: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(items[index:index + size] for index in range(0, len(items), size))


def _unique_listings(listings: list[FeaturedListing]) -> list[FeaturedListing]:
    unique: list[FeaturedListing] = []
    seen: set[tuple[str, ...]] = set()
    for listing in listings:
        key = (
            ("id", listing.id)
            if listing.id
            else ("fallback", listing.title, listing.price, listing.priceUnit)
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(listing)
    return unique


def _order_priority_first(
    listings: list[FeaturedListing], *, prefer_original: bool = False
) -> list[FeaturedListing]:
    """指定小区按配置顺序在前；周边房源保持上游顺序追加。"""
    unique = _unique_listings(listings)
    indexed = list(enumerate(unique))

    def key(item: tuple[int, FeaturedListing]) -> tuple[int, int, int, int]:
        source_index, listing = item
        priority_rank = _PRIORITY_RANK.get(listing.title)
        return (
            0 if priority_rank is not None else 1,
            priority_rank if priority_rank is not None else len(_PRIORITY_RANK),
            0 if prefer_original and listing.original_image else 1,
            source_index,
        )

    return [listing for _index, listing in sorted(indexed, key=key)]


class FeaturedListingsFetcher:
    """从 crm_connector 拉取买卖/租赁房源并缓存，供精选大屏使用。

    crm_connector 上游有配额与限流，这里做进程内缓存，避免大屏轮询打爆
    上游；crm_connector 降级/不可用时返回空数据，由页面回退到静态样例。
    """

    def __init__(
        self,
        crm_base_url: str,
        *,
        cache_seconds: int = 120,
        request_timeout: float = 15.0,
        user_agent: str = "store-media-featured/0.1",
    ):
        self._base_url = crm_base_url.rstrip("/")
        self._cache_seconds = cache_seconds
        self._timeout = request_timeout
        self._user_agent = user_agent
        self._feed: FeaturedFeed | None = None
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def latest(self) -> FeaturedFeed:
        with self._lock:
            cached = self._feed
            if cached is not None and time.monotonic() - self._fetched_at < self._cache_seconds:
                return cached
        fetched = self._fetch()
        if fetched is not None:
            with self._lock:
                self._feed = fetched
                self._fetched_at = time.monotonic()
            return fetched
        with self._lock:
            return self._feed or FeaturedFeed()

    # -- upstream calls ----------------------------------------------------

    def _fetch(self) -> FeaturedFeed | None:
        try:
            sale_page = self._collect_sale()
            rent_page = self._collect_rent()
        except Exception:
            return None
        if sale_page is None and rent_page is None:
            return None
        return FeaturedFeed(
            sale=list(sale_page[0]) if sale_page else [],
            rent=list(rent_page[0]) if rent_page else [],
            sale_total=sale_page[1] if sale_page else None,
            rent_total=rent_page[1] if rent_page else None,
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

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
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            return None

    def _get_json(self, path: str) -> dict[str, Any] | None:
        request = Request(
            f"{self._base_url}{path}",
            headers={"User-Agent": self._user_agent},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            return None

    # -- collection strategy ----------------------------------------------

    def _collect_sale(self) -> tuple[list[FeaturedListing], int | None] | None:
        """买卖：指定小区优先；数据较少时追加圆心 1 公里内房源。"""
        priority: list[FeaturedListing] = []
        for _name, community_id in PRIORITY_COMMUNITIES:
            priority.extend(self._sale_search((community_id,)))

        priority = _unique_listings(priority)
        nearby: list[FeaturedListing] = []
        if len(priority) < NEARBY_EXPANSION_THRESHOLD:
            community_ids = self._nearby_community_ids("sale")
            for chunk in _chunks(community_ids, _MAX_COMMUNITY_IDS_PER_REQUEST):
                nearby.extend(self._sale_search(chunk))

        listings = _order_priority_first([*priority, *nearby])
        if not listings:
            return None
        return listings, len(listings)

    def _collect_rent(self) -> tuple[list[FeaturedListing], int | None] | None:
        """租赁：指定小区优先；数据较少时追加圆心 1 公里内房源。"""
        priority: list[FeaturedListing] = []
        for _name, community_id in PRIORITY_COMMUNITIES:
            priority.extend(self._rent_search((community_id,)))

        priority = _unique_listings(priority)
        nearby: list[FeaturedListing] = []
        if len(priority) < NEARBY_EXPANSION_THRESHOLD:
            community_ids = self._nearby_community_ids("rent")
            for chunk in _chunks(community_ids, _MAX_COMMUNITY_IDS_PER_REQUEST):
                nearby.extend(self._rent_search(chunk))

        listings = _order_priority_first(
            [*priority, *nearby], prefer_original=True
        )
        if not listings:
            return None
        return listings, len(listings)

    def _sale_search(self, community_ids: tuple[str, ...]) -> list[FeaturedListing]:
        listings: list[FeaturedListing] = []
        for page in range(1, MAX_PAGES + 1):
            body = self._post_json(
                "/api/v1/listings/sale/search",
                {"scope": "all", "community_ids": list(community_ids), "page": page},
            )
            if body is None:
                break
            for row in body.get("items") or []:
                listing = self._sale_listing(row)
                if listing is not None:
                    listings.append(listing)
            if not body.get("has_more"):
                break
        return listings

    def _rent_search(self, community_ids: tuple[str, ...]) -> list[FeaturedListing]:
        listings: list[FeaturedListing] = []
        for scope in _RENTAL_SCOPES:
            for page in range(1, MAX_PAGES + 1):
                body = self._post_json(
                    "/api/v1/listings/rental/search",
                    {
                        "scope": scope,
                        "resblock_ids": list(community_ids),
                        "page": page,
                        "page_size": 50,
                    },
                )
                if body is None:
                    break
                for row in body.get("items") or []:
                    listing = self._rent_listing(row)
                    if listing is not None:
                        listings.append(listing)
                if not body.get("has_more"):
                    break
        return listings

    def _nearby_community_ids(self, listing_type: str) -> tuple[str, ...]:
        if listing_type == "sale":
            path = "/api/v1/listings/sale/map/nearby"
            payload: dict[str, Any] = {
                "location": NEARBY_CENTER,
                "radius_meters": NEARBY_RADIUS_METERS,
                "scope": "all",
                "page": 1,
            }
        else:
            path = "/api/v1/listings/rental/map/nearby"
            payload = {
                "location": NEARBY_CENTER,
                "radius_meters": NEARBY_RADIUS_METERS,
                "page": 1,
            }
        body = self._post_json(path, payload)
        if body is None:
            return ()
        return tuple(
            dict.fromkeys(
                str(item).strip()
                for item in (body.get("community_ids") or [])
                if str(item).strip()
            )
        )

    # -- row normalization -------------------------------------------------

    def _sale_listing(self, row: dict[str, Any]) -> FeaturedListing | None:
        image = public_image_url(row.get("surface_image_url"))
        if image is None:
            return None
        community = str(row.get("community") or "").strip()
        if not community:
            return None
        layout = _sale_layout(row.get("layout"))
        area = row.get("area_sqm")
        orient = str(row.get("orientation") or "朝向待定")
        price = _price_number(row.get("total_price_text"), row.get("total_price_yuan"))
        location = " · ".join(
            part
            for part in (str(row.get("biz_circle") or ""), community)
            if part
        ) or community
        subway = " · ".join(
            part
            for part in (
                str(row.get("subway_line") or ""),
                str(row.get("subway_station") or ""),
            )
            if part and part not in ("未知", "无")
        )
        if subway:
            location = f"{location} · {subway}"
        tags = _sale_tags(tuple(str(t) for t in (row.get("tags") or [])))
        extra = " · ".join(t.text for t in tags[:2] if t.type in ("new", "hot", "urgent", "exclusive"))
        description = extra or "真实房源 · 欢迎咨询"
        return FeaturedListing(
            id=str(row.get("listing_id") or ""),
            title=community,
            layout=layout,
            area=f"{area}" if area else "—",
            floor=_format_floor(row.get("floor_type"), row.get("total_floors")),
            orient=orient,
            decor="—",
            price=price,
            priceUnit="万",
            unitPrice=(
                f"{row.get('unit_price_yuan_per_sqm'):.0f}"
                if isinstance(row.get("unit_price_yuan_per_sqm"), (int, float))
                else "—"
            ),
            location=location,
            tags=tags,
            image=image,
            desc=description,
        )

    def _rent_listing(self, row: dict[str, Any]) -> FeaturedListing | None:
        original = original_image_url(row.get("title_image_url"))
        image = original or public_image_url(row.get("title_image_url"))
        if image is None:
            return None
        community = str(row.get("community") or "").strip()
        if not community:
            return None
        layout = str(row.get("layout") or "户型待定")
        area = row.get("area_sqm")
        rent = row.get("monthly_rent_yuan")
        unit_price = (
            f"{rent / area:.0f}"
            if isinstance(rent, (int, float)) and isinstance(area, (int, float)) and area
            else "—"
        )
        del_type = row.get("del_type")
        tags = _rent_tags(del_type if isinstance(del_type, int) else None)
        if del_type == 5:
            description = "省心租托管 · 拎包入住"
        else:
            description = "真实房源 · 拎包入住"
        return FeaturedListing(
            id=str(row.get("listing_id") or ""),
            title=community,
            layout=layout,
            area=f"{area}" if area else "—",
            floor="—",
            orient=str(row.get("orientation") or "朝向待定"),
            decor="—",
            price=f"{rent:.0f}" if isinstance(rent, (int, float)) else "0",
            priceUnit="元/月",
            unitPrice=unit_price,
            location=community,
            tags=tags,
            image=image,
            desc=description,
            original_image=original,
        )
