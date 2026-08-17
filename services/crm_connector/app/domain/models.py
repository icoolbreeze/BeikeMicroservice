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
    # Raw img.ljcdn.com originals returned by the search rows. Fetching the
    # original directly returns 403 for everyone; callers must append a size
    # suffix (.450x.jpg / .750x.jpg / .800x.jpg / .1500x.jpg) to fetch a
    # public variant (see docs/rental-image-cdn.md). Never append here —
    # the raw URL is the stable contract.
    title_image_url: str | None = None          # titleImage 封面图原图
    floor_plan_image_url: str | None = None     # floorPlanImage 户型图原图
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
class TrusteeshipProspectPhoto:
    """One photo from the 托管 detail page's 实勘 list (houseProspectList)."""

    name: str | None
    """Room label (01间-主卧 / 01间-厨房 / 02间-卫生间 …)."""
    url: str | None
    """Absolute img.ljcdn.com path (no size suffix). Lease-image bucket —
    public with an !m_fit,h_630,w_516,l_bk,f_jpg style instruction suffix."""
    primary_flag: bool
    """封面标记 (primaryFlag=1 is the cover photo)."""
    create_time: str | None
    """Upload time as the upstream display string."""


@dataclass(frozen=True)
class TrusteeshipManagerInfo:
    """The 房管人 record from houseHeadInfo.managerInfo."""

    user_name: str | None
    role_name: str | None
    org_name: str | None
    phone: str | None


@dataclass(frozen=True)
class TrusteeshipDeal:
    """One 成交参考 row (deal/list)."""

    deal_price: str | None
    deal_time: str | None
    desc: str | None
    """面积/朝向/楼层/电梯 summary."""
    layout_url: str | None
    prospect_url: str | None
    on_rent_time: str | None


@dataclass(frozen=True)
class TrusteeshipDealPage:
    items: tuple[TrusteeshipDeal, ...]
    page: int
    total: int
    has_more: bool
    request_id: str


@dataclass(frozen=True)
class TrusteeshipListingRow:
    """One 待出租 (waiting-rent) inventory row (house/search/waitingrent).

    ``cell_code`` is the row's ``bizCode`` — verified live to be accepted by
    the pageInfoForPc detail endpoint, so these rows are a self-sufficient
    source of trusteeship ids.
    """

    cell_code: str
    community: str | None
    biz_circle: str | None
    building_name: str | None
    house_name: str | None
    layout_text: str | None
    area_sqm: float | None
    floor: int | None
    guide_price_yuan: int | None
    can_look_time: str | None


@dataclass(frozen=True)
class TrusteeshipListingPage:
    items: tuple[TrusteeshipListingRow, ...]
    page: int
    page_size: int
    total: int
    has_more: bool
    request_id: str


@dataclass(frozen=True)
class TrusteeshipDetail:
    """pageInfoForPc — the 托管 (省心租) detail-page head.

    Captured live 2026-08-15 from trusteeship.link.lianjia.com (品质租赁
    workbench). Covers what the 普租 detailHead cannot: 托管 listings
    (del_type=5) are only served by this domain.
    """

    cell_code: str
    """Trusteeship business code (10612612882101)."""
    house_del_code: str | None
    """The 普租 list search's delCode / listing_id (106126181022)."""
    resblock_name: str | None
    house_name: str | None
    house_type_desc: str | None
    """Layout display text (1-0-1-1)."""
    area_text: str | None
    area_number: float | None
    guide_price_yuan: int | None
    """指导租金 (2000)."""
    orientation: str | None
    floor_type: str | None
    signal_floor: str | None
    total_floor: int | None
    can_live_time: str | None
    viewing_house_time: str | None
    rent_period_desc: str | None
    rent_period_desc_v2: str | None
    tg_end_date: str | None
    delay_days: int | None
    tags: tuple[str, ...]
    manager: TrusteeshipManagerInfo | None
    key_desc: str | None
    """钥匙形态 (智能门锁 …)."""
    has_smart_key: bool | None
    out_show_desc: str | None
    """外网呈现 flag."""
    prospects: tuple[TrusteeshipProspectPhoto, ...]
    """实勘照片 (5 张 for the verified sample)."""
    house_type_images: tuple[str, ...]
    """户型图 absolute URLs."""
    vr_url: str | None
    vr_picture_url: str | None
    hqi_score: str | None
    deal_details: tuple[TrusteeshipDeal, ...]
    deal_avg_price: float | None
    """托管成交参考均价 (trusteeshipDealAvgPrice, 元/月, numeric)."""
    deal_total_count: int | None
    """托管成交参考总条数 (trusteeshipDealTotalCount, numeric)."""
    fee_groups: tuple[str, ...]
    """费用项配置 rows: each row joins the fee matrix cells with ' | '."""
    del_status: int | None
    district_name: str | None


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
    community_ids_truncated: bool
    result: RentalMapPage


# ---------------------------------------------------------------------------
# 买卖 (sale) domain. Upstream: house.link.lianjia.com workbench, captured
# live 2026-08-11 from /search/sale/default/gdiv_mt (docs/sale-api-catalog.md).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SaleListingFilters:
    """Structured filters for the 买卖 全部房源 search (searchQueryNew).

    ``scope`` is the upstream ``vertical`` radio (维护盘/共享盘/… pool), not a
    permission mode like rental's relationRange. ``price_wan``/``area_sqm``
    are comma-free ``lo,hi`` pairs (hi=-1 means open-ended). ``select``
    carries the 筛选 dropdown values keyed by their catalog key
    (appro_broker/key_broker/role/house_stat/…); values are the raw ids the
    catalog returns, and ``-1`` means 不限 so it is dropped before sending.
    """

    scope: str
    community_ids: tuple[str, ...]
    district_id: str | None
    listing_id: str | None
    price_wan: tuple[float | None, float | None] | None
    area_sqm: tuple[float | None, float | None] | None
    rooms: tuple[int, ...]
    floors: tuple[str, ...]
    orientations: tuple[str, ...]
    house_layouts: tuple[str, ...]
    tags: tuple[str, ...]
    select: tuple[tuple[str, str], ...]
    house_age: int | None
    visitable_times: int | None
    payment_mode: str | None
    building_type: str | None
    sort: str
    page: int


@dataclass(frozen=True)
class SaleListingFilterOption:
    """One condition group from the 买卖 getSearchFilters catalog.

    ``value`` mirrors the upstream ``id`` (e.g. price buckets ``50,70``,
    orient codes ``100500000003``, select dropdown keys). Nested ``select``
    groups carry their dropdowns as children whose own children hold the
    id/name pairs.
    """

    key: str | None
    name: str
    value: str | None
    selection_type: str
    default_value: str | None
    for_show: bool
    ext: dict[str, object]
    children: tuple["SaleListingFilterOption", ...]


@dataclass(frozen=True)
class SaleListing:
    """One 在售房源 row from the 买卖 search list (searchQueryNew data.list)."""

    listing_id: str                       # houseDelCode
    community: str                        # communityName
    biz_circle: str | None                # bizCircleName
    layout: str | None                    # unitType 如 2-1-1-1
    area_sqm: float | None                # areaSize
    total_price_yuan: float | None        # totalPrice 总价（元）
    total_price_text: str | None          # totalPriceStr 如 60万
    unit_price_yuan_per_sqm: float | None # unitPrice 单价（元/平）
    floor_desc: str | None                # showFloor 如 低/6
    floor_type: str | None                # floorType 如 底层
    orientation: str | None               # orientation
    tags: tuple[str, ...]                 # tags
    visit_count_15d: int | None           # visitCount 15天带看
    follow_up: bool | None                # followUp 是否有跟进
    create_time: datetime | None          # createTime 录入时间
    maintainer_name: str | None           # maintainerName 维护人
    maintainer_tag: str | None            # maintainerTag 如 A级
    maintain_percentage: int | None       # maintainPercentage 维护完成度
    quality_score: float | None           # qualityScore 评分
    holder_level: str | None              # holderLevel 如 A/B/S
    del_type: int | None                  # delType
    community_id: str | None              # communityId
    payment_mode: str | None              # paymentMode 交易权属
    stat_function: str | None             # statFunction 房屋用途
    subway_line: str | None               # subwayLineName 地铁线
    subway_station: str | None            # subwayName 地铁站
    vr_status: int | None                 # vrStatus
    surface_image_url: str | None         # surfaceImage 封面图
    floor_plan_image_url: str | None      # floorPlanImage 户型图


@dataclass(frozen=True)
class SaleListingPage:
    items: tuple[SaleListing, ...]
    page: int
    total: int
    has_more: bool
    request_id: str


@dataclass(frozen=True)
class SaleCommunitySuggestion:
    """One match from the 买卖 community suggest (sugCommunityInfo)."""

    text: str                    # text 小区名
    community_id: str            # communityId 传回 multi_community_id
    resblock_name: str | None    # resblockName
    resblock_alias: str | None   # resblockAlias 别名
    district_name: str | None    # districtName 城区
    bizcircle_name: str | None   # bizcircleName 商圈
    house_count: int | None      # houseCount（当前为 null）
    del_type: str | None         # delType 1 = 买卖


@dataclass(frozen=True)
class SaleListingDetail:
    """The 买卖 detail head (housedel/views.housedelBaseInfo + basicInfo)."""

    listing_id: str                       # housedelCode
    display_name: str | None              # displayName 小区名
    display_price: str | None             # displayPrice 总价文案 如 60
    latest_price_yuan: float | None       # latestPrice 最新总价（元）
    unit_price_text: str | None           # unitPrice 单价 如 12539
    area_sqm: float | None                # area
    bedroom_amount: int | None            # bedroomAmount
    parlor_amount: int | None             # parlorAmount
    toilet_amount: int | None             # toiletAmount
    cookroom_amount: int | None           # cookroomAmount
    display_floor: str | None             # displayFloor 如 低/6
    orientation: str | None               # orientation
    del_grade: str | None                 # delGrade 房屋等级
    broker_grade: str | None              # brokerGrade 如 A级:64.1分
    holder_name: str | None               # holderInfo.name 维护人
    holder_org: str | None                # holderInfo.orgName 维护门店
    last_days: str | None                 # lastDays 挂牌天数
    ctime: str | None                     # ctime 录入时间文案
    house_origin: str | None              # houseOrigin 房源来源
    house_id: str | None                  # houseId 内部 ID
    acn_house_id: str | None              # acnHouseId
    resblock_id: str | None               # resblockId
    res_block_info: str | None            # resBlockInfo 小区(城区-商圈)
    vr_status: int | None                 # vrStatus
    owner_reserve_price: str | None       # ownerReservePrice 业主预期价
    inventory_score: str | None           # inventoryScore 库存分
    del_status: int | None                # housedelStatus
    is_credential_completed: bool | None  # isCredentialCompleted
    # basicInfo（小区/楼栋属性）
    district_name: str | None             # districtName
    biz_circle: str | None                # bizcircleName
    build_year: int | None                # buildYear
    build_type: str | None                # buildType 建筑类型
    build_struct: str | None              # buildStruct 建筑结构
    deal_prop: str | None                 # dealProp 交易权属
    house_usage: str | None               # houseUsage 房屋用途
    tenement_fee: str | None              # tenementFee 物业费
    heat_fee: str | None                  # heatFee 供暖费
    gas_fee: str | None                   # gasFee 燃气费
    water_type: str | None                # waterType 用水
    electric_type: str | None             # eletricType 用电
    heat_type: str | None                 # heatType 供暖
    has_gas: str | None                   # hasGas
    has_hot_water: str | None             # hasHotWater
    has_mid_water: str | None             # hasMidWater
    mid_water_fee: str | None             # midWaterFee
    hot_water_fee: str | None             # hotWaterFee
    car_ratio: str | None                 # carRatio 车位比
    car_onground: int | None              # carOnground 地上车位数
    car_underground: int | None           # carUnderground 地下车位数
    park_fee: str | None                  # parkFee 停车费
    has_lift: str | None                  # hasLift 是否有电梯
    lift_house_ratio: str | None          # liftHouseRatio 梯户比
    school_info: str | None               # schoolInfo 学区
    prop_years: str | None                # propYears 产权年限
    building_disgust: str | None          # buildingDisgust 凶宅/嫌恶
    # extInfo（housedelExtInfo，外网呈现）
    external_url_lianjia: str | None      # lianjiaUrl
    external_url_beike: str | None        # beikeUrl
    vr_url: str | None                    # vrUrl
    net_work_status: int | None           # netWorkStatus 外网呈现状态


@dataclass(frozen=True)
class SaleMaintainField:
    """One rendered field in the 买卖 getMaintainInfo modules.

    ``value`` is the render-ready display text; ``comment`` is the page's
    side note (如 与房产证一致); ``important`` marks key fields.
    """

    key: str
    name: str
    value: str | None
    important: bool
    comment: str | None


@dataclass(frozen=True)
class SaleMaintainModule:
    """One grouped section of the 买卖 维护信息 (getMaintainInfo.maintainList).

    Each module carries 看房信息/价格信息/业主信息/特色信息 sections as
    rows of field lists.
    """

    name: str
    fields: tuple[SaleMaintainField, ...]


@dataclass(frozen=True)
class SaleMaintainInfo:
    """getMaintainInfo — the 买卖 detail page's 维护信息 section.

    ``modules`` mirrors maintainBasicInfo.maintainList; ``important_fields``
    mirrors importantBasicInfo.importantList (重点字段); ``complete_rate`` is
    the 9/9 完备率 text and ``last_update_time`` the last edit timestamp.
    """

    listing_id: str
    modules: tuple[SaleMaintainModule, ...]
    important_fields: tuple[SaleMaintainField, ...]
    complete_rate: str | None
    last_update_time: datetime | None
    remark: str | None


@dataclass(frozen=True)
class SaleFollowRecord:
    """One 跟进 record from the 买卖 detailFollow (housedelfollow/queryfollows).

    ``creator_name`` includes the role and 门店 (如 钟俊(维护人) - …店A组 -
    13002871555); ``create_time`` is the page's display text (2026-08-07
    16:58); ``follow_label`` carries tags such as 客户有意向.
    """

    follow_id: int | None                 # id
    content: str | None                   # followContent
    creator_name: str | None              # creatorName
    create_time: str | None               # createTime 文案
    on_top: bool                          # onTop 置顶
    remarks: str | None                   # remarks
    follow_label: str | None              # followLabel
    video_url: str | None                 # videoUrl


# ---------------------------------------------------------------------------
# 买卖 地图找房 (sale mapSearch) domain. Upstream: house.link.lianjia.com
# /search/sale/mapSearch (docs/sale-api-catalog.md §地图找房), captured live
# 2026-08-11. The sale map domain lives on house.link itself (unlike the
# rental map which proxies map.ke.com).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SaleMapSuggestion:
    """One match from the 买卖 map suggest (/search/map/suggest).

    Unlike the rental map's typed suggestions, the sale suggest returns
    community entries only; ``type`` is always "community" in current
    observations. ``latitude``/``longitude`` are the centroid used to centre
    the map view and drive radius searches.
    """

    suggestion_id: str            # id
    text: str                     # text 显示名（如 华润广场(成华)）
    alias: str | None             # alias 别名（如 万象城）
    bizcircle_name: str | None    # bizcircleName 商圈
    district_name: str | None     # districtName 城区
    item_type: str                # type（当前恒为 community）
    count: int | None             # count 在售套数
    latitude: float | None        # latitude
    longitude: float | None       # longitude
    unit_price: float | None      # unitPrice 均价


@dataclass(frozen=True)
class SaleMapBubble:
    """One bubble from the 买卖 map bubbleSearch (district or community level)."""

    bubble_id: str                # id（community 级即小区 id，回灌 multi_community_id）
    name: str                     # name
    count: int | None             # count 在售套数
    unit_price: float | None      # unit_price 均价（元/平）
    latitude: float | None        # latitude
    longitude: float | None       # longitude
    desc: str | None              # desc


@dataclass(frozen=True)
class SaleMapBubbleFilters:
    """Bounds + level for the 买卖 map bubbleSearch.

    ``group_type`` is "district" or "community". ``filters`` is the page's
    JSON filter blob; an empty dict is the valid no-filter value.
    """

    city_id: str
    bounds: MapBounds
    group_type: str
    filters: dict[str, object]


@dataclass(frozen=True)
class SaleMapNearbySearchFilters:
    """Human-oriented 买卖 nearby search centred on a resolved map location.

    Mirrors the rental nearby flow: resolve a place, load community bubbles
    for its enclosing rectangle, select community ids inside the radius by
    Haversine distance, then hand those ids back to the list search
    (sale_listing_search.community_ids → multi_community_id). Not a
    property-coordinate radius query — community centroids only.
    """

    location: str
    center_latitude: float | None
    center_longitude: float | None
    radius_meters: int
    scope: str
    price_wan: tuple[float | None, float | None] | None
    area_sqm: tuple[float | None, float | None] | None
    rooms: tuple[int, ...]
    page: int


@dataclass(frozen=True)
class SaleMapNearbySearchResult:
    center: SaleMapSuggestion
    radius_meters: int
    matched_community_count: int
    community_ids: tuple[str, ...]
    community_ids_truncated: bool
    result: SaleListingPage
