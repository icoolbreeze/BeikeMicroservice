from __future__ import annotations

from app.domain.models import Role, Store, User


ROLE_LABELS = {
    Role.SYSTEM_ADMIN: "系统管理员",
    Role.REGIONAL_MANAGER: "区域经理",
    Role.STORE_MANAGER: "店长",
    Role.STAFF: "店员",
}

ROLE_PERMISSIONS = {
    Role.SYSTEM_ADMIN: ("users:manage", "stores:manage", "media:manage", "display:view"),
    Role.REGIONAL_MANAGER: ("users:manage:region", "stores:manage:region", "media:manage:region", "display:view"),
    Role.STORE_MANAGER: ("users:manage:store", "media:manage:store", "display:view"),
    Role.STAFF: ("display:view",),
}


def can_access_store(user: User, store: Store, *, write: bool = False) -> bool:
    if not user.is_active or not store.is_active:
        return False
    if user.role is Role.SYSTEM_ADMIN:
        return True
    if user.role is Role.REGIONAL_MANAGER:
        return user.region_id == store.region_id
    if user.role is Role.STORE_MANAGER:
        return user.store_id == store.id
    if user.role is Role.STAFF:
        return not write and user.store_id == store.id
    return False


def can_manage_role(actor: User, target_role: Role) -> bool:
    if actor.role is Role.SYSTEM_ADMIN:
        return True
    if actor.role is Role.REGIONAL_MANAGER:
        return target_role in {Role.STORE_MANAGER, Role.STAFF}
    if actor.role is Role.STORE_MANAGER:
        return target_role is Role.STAFF
    return False


def can_manage_user(actor: User, target: User) -> bool:
    if not can_manage_role(actor, target.role):
        return False
    if actor.role is Role.SYSTEM_ADMIN:
        return True
    if actor.role is Role.REGIONAL_MANAGER:
        return actor.region_id == target.region_id
    if actor.role is Role.STORE_MANAGER:
        return actor.store_id == target.store_id
    return False
