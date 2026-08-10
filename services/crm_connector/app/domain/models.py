from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ConnectionState(str, Enum):
    READY = "ready"
    EXPIRING = "expiring"
    AUTH_REQUIRED = "auth_required"
    NETWORK_REQUIRED = "network_required"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class ProviderStatus:
    state: ConnectionState
    message: str
    expires_at: datetime | None = None
    """The credential validity deadline, when one is locally known; ``None``
    when no credential exists or its deadline is unknown."""


@dataclass(frozen=True)
class ConnectionStatus:
    state: ConnectionState
    message: str
    bound_employee_principal: str | None
    mcp_transport: str
    checked_at: datetime
    credential_expires_at: datetime | None = None
    """When the active credential's validity period ends; ``None`` when the
    connector holds no credential (or the deadline is unknown)."""


@dataclass(frozen=True)
class Principal:
    employee_principal: str
    display_name: str | None = None


@dataclass(frozen=True)
class RentalListingFilters:
    community_keyword: str | None
    resblock_ids: tuple[str, ...]
    listing_id: str | None
    scope: str
    monthly_rent_yuan: tuple[float | None, float | None] | None
    area_sqm: tuple[float | None, float | None] | None
    rooms: tuple[int, ...]
    orientations: tuple[str, ...]
    page: int
    page_size: int
    condition_filters: tuple[tuple[str, str | int | float], ...] = ()


@dataclass(frozen=True)
class RentalListingFilterOption:
    """One server-defined condition from the 房源列表 filter catalog."""

    key: str | None
    name: str
    value: str | int | float | None
    selection_type: str
    default_value: str | int | float | None
    children: tuple["RentalListingFilterOption", ...]


@dataclass(frozen=True)
class RentalListing:
    listing_id: str
    community: str
    layout: str | None
    area_sqm: float | None
    monthly_rent_yuan: float | None
    orientation: str | None
    visible_scope: str
    # Upstream delType: 2 = 普租, 5 = 托管.  Only 普租 houses have a
    # detailHead record; callers should not attempt detail for 托管 ids.
    del_type: int | None = None
    # --- detail-only fields, populated by rental_listing_get_detail ---
    # (search rows leave them None; see docs/rental-api-catalog.md §房源详情)
    maintain_org: str | None = None          # orgName 维护门店
    source: str | None = None                # delResourceSub 房源来源
    floor_desc: str | None = None            # floorDesc 楼层描述
    total_floors: int | None = None          # totalFloor 总楼层
    listed_days: int | None = None           # alreadyCreateDays 已录入天数
    house_grade: str | None = None           # houseGrade 房源评级
    follow_total: int | None = None          # followTotal 累计跟进次数
    follow_last_7d: int | None = None        # followNum7Days 近7天跟进
    showing_total: int | None = None         # showingTotal 累计带看次数
    showing_last_7d: int | None = None       # showingNum7Days 近7天带看
    external_url_ke: str | None = None       # keUrl 外网房源链接
    external_url_lianjia: str | None = None  # lianJiaUrl 外网房源链接
    has_key: bool | None = None              # haveKey 是否取到钥匙
    del_status_text: str | None = None       # delStatusString 有效状态
    house_id: str | None = None              # houseId 内部房源 ID


@dataclass(frozen=True)
class RentalListingPage:
    items: tuple[RentalListing, ...]
    page: int
    page_size: int
    has_more: bool
    request_id: str


@dataclass(frozen=True)
class ProspectPhoto:
    """One photo from the detail-page 实勘 (on-site survey) image list."""

    url: str
    room_name: str | None
    image_type: str
    """Upstream imageType: REAL = 实勘图, TITLE = 标题图."""
    upload_user: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class ListingProspect:
    """The detail-page prospect (实勘) record for one listing."""

    listing_id: str
    photos: tuple[ProspectPhoto, ...]
    floor_plan_url: str | None
    can_edit: bool | None
    has_survey_photo: bool
    """True when at least one REAL (实勘) photo has been uploaded."""


@dataclass(frozen=True)
class ListingPropertyInfo:
    """detailHdicInfo — the detail page's 小区/楼栋 property attributes.

    Fields mirror the page's 基础信息 grouping (verified in DOM 2026-08-09):
    小区信息 (community/district/biz_circle/tenement_fee…), 建筑信息
    (building_*, deal_property, disgust/haunted…), 生活信息
    (water/electric/gas/heating fees, hot/middle water, parking counts…).
    """

    listing_id: str
    # -- 小区信息
    community: str | None = None           # resblockName
    district: str | None = None            # districtName 所在城区
    biz_circle: str | None = None          # bizCircleName 所属商圈
    tenement_fee: str | None = None        # tenementFeeStr 物业费
    kindergarten: str | None = None        # kindergarten 小区幼儿园
    # -- 建筑信息
    building_type: str | None = None       # buildTypeName 建筑类型
    building_structure: str | None = None  # buildingStructureName 建筑结构
    building_year: int | None = None       # buildingYear 建筑年代
    property_purpose: str | None = None    # statFunctionName 房屋用途
    deal_property: str | None = None       # dealPropertyName 交易权属
    age_limit: str | None = None           # propertyAgeLimitName 产权年代
    disgust_desc: str | None = None        # disgustDesc 凶宅信息
    haunted_desc: str | None = None        # hauntedDesc 嫌恶设施
    # -- 生活信息
    elevator: str | None = None            # elevatorCntStr 是否有电梯
    ti_hu_ratio: str | None = None         # tiHuRatio 梯户比例
    water_type: str | None = None          # waterTypeName 用水类型
    electric_type: str | None = None       # electricTypeName 用电类型
    heating: str | None = None             # heatingTypeName 供暖类型
    heating_fee: str | None = None         # heatingFeeStr 供暖费用
    gas: str | None = None                 # gasStr 是否有燃气
    gas_fee: str | None = None             # gasFeeStr 燃气费
    hot_water: str | None = None           # hotWaterStr 是否有热水
    hot_water_fee: str | None = None       # hotWaterFeeStr 热水费
    middle_water: str | None = None        # middleWaterStr 是否有中水
    middle_water_fee: str | None = None    # middleWaterFeeStr 中水费
    parking_ratio: str | None = None       # carRatio 车位比例
    parking_fee: str | None = None         # parkingFee 停车服务费
    parking_above_ground: str | None = None  # carUpCntStr 地上车位数
    parking_underground: str | None = None   # carDownCntStr 地下车位数
    green_rate: float | None = None        # greenRate 绿化率
    cubage_rate: float | None = None       # cubageRate 容积率


@dataclass(frozen=True)
class HqiHeatItem:
    """One heat/visit metric row from the HQI tab."""

    name: str              # dataName
    value: str | None      # dataValue
    fluctuate: str | None  # fluctuateVal
    positive: bool | None  # isPositive 1/0


@dataclass(frozen=True)
class HqiSuggestion:
    """One AI-generated optimization suggestion from the HQI tab."""

    item: str | None          # optimizeItemName
    suggestion: str | None    # suggestionDesc


@dataclass(frozen=True)
class HqiScore:
    """The detail page's HQI quality-score record (detailHqiTab)."""

    total_score: str | None             # totalScoreValue
    level: str | None                   # totalScoreLevel
    next_level: str | None              # nextLevelName
    rank_text: str | None               # rankDescPrefix + rankDescSuffix
    pending_optimize: str | None        # pendingOptimizeDesc
    heat_items: tuple[HqiHeatItem, ...]
    suggestions: tuple[HqiSuggestion, ...]


@dataclass(frozen=True)
class MaintainField:
    """One rendered field inside a maintain-info module (getMaintainInfo).

    ``display_value`` is the upstream-rendered display text (e.g. "精装",
    "随时入住", "床/衣柜/桌椅…"), ready to show without further mapping.
    """

    name: str                    # fieldName 字段名（可入住时间/家具/租期…）
    display_value: str | None    # displayValue 渲染后的值
    complete: bool | None        # complete 是否已完备


@dataclass(frozen=True)
class MaintainModule:
    """One grouped section of the detail page's 维护信息 (maintain info)."""

    rate_text: str | None                 # completenessRate "完备率：9/9(100%)"
    fields: tuple[MaintainField, ...]


@dataclass(frozen=True)
class ListingMaintainInfo:
    """getMaintainInfo — the detail page's 维护信息 section.

    Modules carry the render-ready display values for important fields
    (家具/家电/租期/装修/看房时间/入住时间…) and the rest; ``remark`` is
    the maintainer's free-text note shown on the detail page.
    """

    listing_id: str
    modules: tuple[MaintainModule, ...]
    remark: str | None                     # remark 备注
    all_field_rate: int | None             # allFieldMaintainRate 整体完备率
    important_rate: int | None             # importantFieldMaintainRate 重点字段完备率
    owner_lowest_price: str | None         # ownerLowestPrice 业主底价


@dataclass(frozen=True)
class FollowRecord:
    """One 跟进 (follow-up) record from detailFollow's result list.

    The content text carries the house's latest status — 备注提醒、钥匙情况
    (钥匙类型/可否快速带看/预计交钥匙时间)、是否可马上看房 are written by the
    keeper/云管家 into ``followUpContent``; ``on_top`` marks the pinned
    (置顶) important follow-up.
    """

    content: str | None                    # followUpContent 跟进描述（含钥匙/看房等状态）
    follow_type: str | None                # followTypeStr 跟进类型（普通跟进/…）
    creator_name: str | None               # creatorName 跟进人
    role: str | None                       # roleTypeStr 角色（维护人/…）
    created_at: datetime | None            # createTime（毫秒时间戳）
    labels: tuple[str, ...]                # followLabel 标签（真实在租/…）
    label_code: str | None                 # followLabelCode（IN_RENT/…）
    remarks: str | None                    # remarks 独立备注栏
    on_top: bool                           # onTop 是否置顶（重点跟进）
    on_top_time: datetime | None           # onTopTime 置顶时间（毫秒时间戳）


@dataclass(frozen=True)
class ListingDetailInfo:
    """Aggregated detail-page information beyond detailHead.

    Composed from five upstream records: getHouseLabel (labels),
    detailHdicInfo (property attributes), detailHqiTab (quality score),
    getMaintainInfo (维护信息), detailFollow (跟进记录).
    ``hqi`` is None when the house has no HQI record yet — an empty
    ``detailHqiTab.data`` is the upstream's honest "no score" answer.
    ``maintain`` / ``follows`` are only populated by the extended
    aggregation (house_info); None when that part is not requested.
    """

    listing_id: str
    labels: tuple[str, ...]
    property_info: ListingPropertyInfo | None
    hqi: HqiScore | None
    maintain: ListingMaintainInfo | None = None
    follows: tuple[FollowRecord, ...] = ()


@dataclass(frozen=True)
class MapBounds:
    min_longitude: float
    max_longitude: float
    min_latitude: float
    max_latitude: float


@dataclass(frozen=True)
class RentalMapSearchFilters:
    city_id: str
    data_source: str
    bounds: MapBounds
    page: int
    mode: str
    condition_tokens: tuple[str, ...]
    result_type: str | None
    resblock_id: str | None
    resblock_ids: tuple[str, ...]


@dataclass(frozen=True)
class RentalMapListing:
    listing_id: str
    title: str
    description: str
    tags: tuple[str, ...]
    price_text: str | None
    unit_price_text: str | None


@dataclass(frozen=True)
class RentalMapPage:
    items: tuple[RentalMapListing, ...]
    page: int
    total: int
    has_more: bool
    mode: str
    request_id: str


@dataclass(frozen=True)
class RentalMapBubbleFilters:
    city_id: str
    data_source: str
    bounds: MapBounds
    group_type: str
    group_id: str | None
    condition_tokens: tuple[str, ...]


@dataclass(frozen=True)
class RentalMapBubble:
    bubble_id: str
    name: str
    group_type: str
    latitude: float | None
    longitude: float | None
    count: int | None
    count_text: str | None
    price_text: str | None


@dataclass(frozen=True)
class RentalMapSuggestionFilters:
    city_id: str
    data_source: str
    query: str


@dataclass(frozen=True)
class RentalMapSuggestion:
    item_type: str
    item_type_name: str | None
    item_id: str
    name: str
    count_text: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class RentalMapNearbySearchFilters:
    """Human-oriented search centred on a resolved map location.

    The map upstream only accepts a collection of community identifiers for
    drawn-area searches.  ``radius_meters`` is therefore applied to community
    centroids by the application service before requesting the house list.
    """

    city_id: str
    data_source: str
    location: str
    center_latitude: float | None
    center_longitude: float | None
    radius_meters: int
    price_min_yuan: int | None
    price_max_yuan: int | None
    rooms: tuple[int, ...]
    rental_modes: tuple[str, ...]
    page: int


@dataclass(frozen=True)
class RentalMapNearbySearchResult:
    center: RentalMapSuggestion
    radius_meters: int
    matched_community_count: int
    community_ids: tuple[str, ...]
    result: RentalMapPage
