"""清水房采集的行模型与变更点哈希（`docs/roughcast-quality-ranking.md` §4.5）。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class RoughcastRow:
    """队列 A 落库的一行。字段与 `roughcast_listing_current` 的业务列一一对应。

    与 `RoughcastRentalListing`（手机列表的 8 字段展示白名单）**不是同一个东西**：
    这是入库模型，字段多且不对外暴露；那个是对外投影。两者不得互相替代。
    """

    listing_id: str
    community_name: str
    community_id: str | None = None
    resblock_id: str | None = None
    bizcircle: str | None = None          # 第 1 期恒 None，见 roughcast_schema
    layout: str | None = None
    rooms: int | None = None
    halls: int | None = None
    baths: int | None = None
    area_sqm: float | None = None
    monthly_rent_yuan: float | None = None
    orientation: str | None = None
    floor_desc: str | None = None
    total_floors: int | None = None
    rent_mode: str | None = None
    del_type: int | None = None
    fitment_status: str | None = None     # 原值：'002' 毛坯 / '001' 简装 / '003' 精装 / None / ''
    fitment_status_desc: str | None = None
    create_time: str | None = None        # UTC ISO 绝对时间戳，绝不存天数（4.4 规则 3）
    title_image_url: str | None = None


BUSINESS_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(RoughcastRow) if f.name != "listing_id"
)

# 4.5 指定的 10 元组。fitment_status 故意不在内：队列 A 带 fitment=002 过滤，
# 装修状态变了房源会直接从结果集消失，由 is_active=0 表达。
HASH_FIELDS: tuple[str, ...] = (
    "monthly_rent_yuan", "area_sqm", "rooms", "halls", "baths",
    "orientation", "floor_desc", "total_floors", "rent_mode", "del_type",
)

_NUMERIC_HASH_FIELDS = frozenset({
    "monthly_rent_yuan", "area_sqm", "rooms", "halls", "baths", "total_floors", "del_type",
})

_MISSING = "~"  # None 的唯一表示，必须与空字符串区分开


def _normalize(field: str, value: object) -> str:
    """把取值规范化成唯一的字符串表示。

    没有这一步，`4300` / `4300.0` / `"4300"` 会算出三个不同的哈希，于是每轮采集
    都被判成变更点，4.5 那 36 倍的收益归零——而症状只是「快照表长得有点快」。
    """
    if value is None:
        return _MISSING
    if field in _NUMERIC_HASH_FIELDS:
        try:
            return f"{round(float(value), 2):.2f}"
        except (TypeError, ValueError):
            # 上游偶尔在数值字段塞非数字（见 V2.3 的数据脏点）。降级按文本处理，
            # 不抛异常：一行脏数据不该中断整轮采集。
            pass
    return str(value).strip()


def content_hash(row: RoughcastRow) -> str:
    payload = "\x1f".join(
        f"{field}={_normalize(field, getattr(row, field))}" for field in HASH_FIELDS
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def business_values(row: RoughcastRow) -> tuple[object, ...]:
    data = asdict(row)
    return tuple(data[field] for field in BUSINESS_FIELDS)


def community_key(row: RoughcastRow) -> str | None:
    """小区的本地主键。

    4.1:`resblock_id` 解析出来之前用小区名规范化后的哈希占位。占位是必要的——
    没有本地主键,每轮就得靠 `resblock_name` 字符串对齐,改名或同名小区会串。
    """
    if row.resblock_id:
        return str(row.resblock_id)
    name = (row.community_name or "").strip()
    if not name:
        return None
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return f"name:{digest}"
