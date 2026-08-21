"""队列 A 的 connector 客户端（`docs/roughcast-quality-ranking.md` §三 / §六）。

与 `roughcast_rental_fetcher.RoughcastRentalFetcher` **刻意分开**,两点区别是硬要求:

1. 那个是手机列表的 8 字段展示白名单投影;这个读类型化全字段行 + `total`,
   两者不得互相替代。
2. **这个类里没有任何详情接口路径。** 第三章禁止抓取阶段做楼层详情回补
   （`_fill_missing_floors` 会对每行 `del_type == 2` 打一次详情,全量场景放大成
   上千次额外请求）。禁令因此是「代码里不存在这条路」,不是「记得别调用」。

错误也不同:展示投影把任何失败都吞成 `None`(页面不能空窗),而采集必须把
HTTP 状态码和错误码原样交给熔断器——它们正是第三章四条触发信号的观测形式。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.domain.roughcast import RoughcastRow, community_key

CRAWL_SCOPE = "all"
CRAWL_FITMENT_CODE = "002"
CRAWL_PAGE_SIZE = 50           # 上游单页上限,第三章按它算页数
SEARCH_PATH = "/api/v1/listings/rental/search"


@dataclass(frozen=True)
class CrawlPage:
    rows: tuple[RoughcastRow, ...]
    page: int
    total: int
    has_more: bool


class CrawlRequestError(RuntimeError):
    """一次采集请求失败。`http_status` / `error_code` 供熔断器判定。"""

    def __init__(self, message: str, *, http_status: int | None = None,
                 error_code: str | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code


class RoughcastCrawlClient:
    def __init__(self, crm_base_url: str, *, page_size: int = CRAWL_PAGE_SIZE,
                 request_timeout: float = 30.0,
                 user_agent: str = "store-media-roughcast-crawler/1.0"):
        self._base_url = crm_base_url.rstrip("/")
        self._page_size = page_size
        self._timeout = request_timeout
        self._user_agent = user_agent

    @property
    def page_size(self) -> int:
        return self._page_size

    def search_page(self, page: int) -> CrawlPage:
        body = self._post_json(SEARCH_PATH, {
            "scope": CRAWL_SCOPE,
            "condition_filters": {"fitment": CRAWL_FITMENT_CODE},
            "page": page,
            "page_size": self._page_size,
        })
        items = body.get("items")
        if not isinstance(items, list):
            raise CrawlRequestError(
                f"第 {page} 页响应缺少 items 数组", error_code="CRM_UPSTREAM_CHANGED"
            )
        rows = tuple(
            row
            for raw in items
            if isinstance(raw, dict)
            for row in (_row_from_response(raw),)
            if row is not None
        )
        total = body.get("total")
        return CrawlPage(
            rows=rows,
            page=page,
            total=int(total) if isinstance(total, int) else 0,
            has_more=bool(body.get("has_more")),
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": self._user_agent},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise CrawlRequestError(
                f"connector 返回 HTTP {exc.code}",
                http_status=exc.code,
                error_code=_error_code(exc),
            ) from exc
        except (URLError, OSError) as exc:
            # 网络类失败不是熔断的即时信号,靠「连续 3 次失败」兜底。
            raise CrawlRequestError(f"connector 不可达:{exc}") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise CrawlRequestError(
                f"connector 响应不是 JSON:{exc}", error_code="CRM_UPSTREAM_CHANGED"
            ) from exc
        if not isinstance(body, dict):
            raise CrawlRequestError("connector 响应不是对象", error_code="CRM_UPSTREAM_CHANGED")
        return body


def _error_code(exc: HTTPError) -> str | None:
    """从 connector 的 `{"detail": {"code": ..., "message": ...}}` 里取出错误码。

    `CRM_AUTH_REQUIRED`(session 失效)与 `CRM_UPSTREAM_CHANGED`(验证码/登录页)
    是熔断的两条即时信号,不能只看 HTTP 状态码。
    """
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        code = detail.get("code")
        return str(code) if code else None
    return None


def _row_from_response(raw: dict[str, Any]) -> RoughcastRow | None:
    listing_id = raw.get("listing_id")
    if not isinstance(listing_id, str) or not listing_id.strip():
        return None
    community_name = _text(raw.get("resblock_name")) or _text(raw.get("community"))
    if not community_name:
        return None
    row = RoughcastRow(
        listing_id=listing_id.strip(),
        community_name=community_name,
        resblock_id=_text(raw.get("resblock_id")),
        # connector 改动 E 已落地,直接取原值。空串按未知处理(与 fitment_status
        # 不同:商圈没有「''与 None 语义不同」的问题,见 domain/roughcast.py)。
        bizcircle=_text(raw.get("biz_circle")),
        layout=_text(raw.get("layout")),
        rooms=_integer(raw.get("bedroom_amount")),
        halls=_integer(raw.get("hall_amount")),
        baths=_integer(raw.get("bathroom_amount")),
        area_sqm=_number(raw.get("area_sqm")),
        monthly_rent_yuan=_number(raw.get("monthly_rent_yuan")),
        orientation=_text(raw.get("orientation")),
        floor_desc=_text(raw.get("floor_desc")),
        total_floors=_integer(raw.get("total_floors")),
        rent_mode=_text(raw.get("rent_mode_label")),
        del_type=_integer(raw.get("del_type")),
        # 原值,包括 None 与空串。§七.8:布尔会把简装/精装/未知压成同一个值。
        fitment_status=raw.get("fitment_status") if isinstance(raw.get("fitment_status"), str)
        else None,
        fitment_status_desc=_text(raw.get("fitment_status_desc")),
        # connector 序列化的 UTC ISO 串,原样存;天数在查询时派生(4.4 规则 3)。
        create_time=_text(raw.get("create_time")),
        # 原图 URL 是稳定契约,不在这里加尺寸后缀——那是展示层的事。
        title_image_url=_text(raw.get("title_image_url")),
    )
    return RoughcastRow(**{**row.__dict__, "community_id": community_key(row)})


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None
