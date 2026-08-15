from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModuleStatus(str, Enum):
    IMPLEMENTED = "implemented"
    RESERVED = "reserved"


@dataclass(frozen=True)
class CrmModule:
    module_id: str
    name: str
    parent_id: str | None
    status: ModuleStatus
    note: str


_MODULES = (
    CrmModule("property", "房源", None, ModuleStatus.IMPLEMENTED, "房源业务域入口"),
    CrmModule("property.rental", "租赁", "property", ModuleStatus.IMPLEMENTED, "第一期只读查询"),
    CrmModule(
        "property.rental.map_search",
        "地图找房",
        "property.rental",
        ModuleStatus.IMPLEMENTED,
        "地点联想、气泡加载、可视范围、画圈与附近圆形查询",
    ),
    CrmModule(
        "property.rental.listing_search",
        "房源列表（全部房源）",
        "property.rental",
        ModuleStatus.IMPLEMENTED,
        "页面路由名为房源列表，当前选中页签为全部房源；已接入实时筛选字典与受控条件查询",
    ),
    CrmModule(
        "property.rental.tuoguan",
        "托管（省心租）",
        "property.rental",
        ModuleStatus.IMPLEMENTED,
        "trusteeship.link.lianjia.com 省心租工作台：详情头（实勘照片/户型图/VR/费用项/成交参考）与成交记录",
    ),
    CrmModule(
        "property.sale",
        "买卖",
        "property",
        ModuleStatus.IMPLEMENTED,
        "house.link.lianjia.com 买卖工作台；已接入筛选字典、小区联想、受控条件查询与详情/维护/跟进",
    ),
    CrmModule(
        "property.sale.listing_search",
        "买卖房源（全部房源）",
        "property.sale",
        ModuleStatus.IMPLEMENTED,
        "searchQueryNew 受控查询 + getSearchFilters 筛选字典 + sugCommunityInfo 小区联想",
    ),
    CrmModule(
        "property.sale.listing_detail",
        "买卖房源详情",
        "property.sale",
        ModuleStatus.IMPLEMENTED,
        "housedel/views 详情头 + getMaintainInfo 维护信息 + queryfollows 跟进记录",
    ),
    CrmModule(
        "property.sale.map_search",
        "买卖地图找房",
        "property.sale",
        ModuleStatus.IMPLEMENTED,
        "house.link 地图域：地点联想、小区气泡、半径内小区过滤并回灌列表搜索",
    ),
    CrmModule("property.new_home", "新房", "property", ModuleStatus.RESERVED, "后续接入"),
    CrmModule("property.commercial", "商铺写字楼", "property", ModuleStatus.RESERVED, "后续接入"),
    CrmModule("property.community", "小区", "property", ModuleStatus.RESERVED, "后续接入"),
    CrmModule("customers", "客源", None, ModuleStatus.RESERVED, "后续接入"),
    CrmModule("leads", "线索", None, ModuleStatus.RESERVED, "后续接入"),
    CrmModule("signing", "签约", None, ModuleStatus.RESERVED, "后续接入"),
    CrmModule("after_signing", "签后", None, ModuleStatus.RESERVED, "后续接入"),
    CrmModule("applications", "应用", None, ModuleStatus.RESERVED, "后续接入"),
)


def crm_modules() -> tuple[CrmModule, ...]:
    return _MODULES
