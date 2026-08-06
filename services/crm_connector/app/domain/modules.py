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
    CrmModule("property.sale", "买卖", "property", ModuleStatus.RESERVED, "后续接入"),
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
