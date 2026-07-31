from datetime import UTC, datetime

from app.domain.models import Role, Store, User
from app.domain.policies import can_access_store, can_manage_role


def user(role: Role, region: str | None = None, store: str | None = None) -> User:
    return User("u", "user", "用户", role, region, store, True, datetime.now(UTC))


def test_role_hierarchy() -> None:
    assert can_manage_role(user(Role.SYSTEM_ADMIN), Role.REGIONAL_MANAGER)
    assert can_manage_role(user(Role.REGIONAL_MANAGER, "west"), Role.STORE_MANAGER)
    assert can_manage_role(user(Role.STORE_MANAGER, "west", "s1"), Role.STAFF)
    assert not can_manage_role(user(Role.STORE_MANAGER, "west", "s1"), Role.REGIONAL_MANAGER)
    assert not can_manage_role(user(Role.STAFF, "west", "s1"), Role.STAFF)


def test_store_access_is_scoped() -> None:
    store = Store("s1", "一店", "west", True, datetime.now(UTC))
    assert can_access_store(user(Role.REGIONAL_MANAGER, "west"), store, write=True)
    assert not can_access_store(user(Role.REGIONAL_MANAGER, "east"), store, write=True)
    assert can_access_store(user(Role.STORE_MANAGER, "west", "s1"), store, write=True)
    assert not can_access_store(user(Role.STAFF, "west", "s1"), store, write=True)
