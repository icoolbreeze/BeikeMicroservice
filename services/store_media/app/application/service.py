from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from app.domain.models import MediaItem, MediaType, Role, Store, User
from app.domain.policies import can_access_store, can_manage_role, can_manage_user
from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.security.passwords import hash_password, new_session_token, token_digest, verify_password
from app.security.uploads import detect_media

_SCOPE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ServiceError(Exception):
    status_code = 400


class AuthenticationError(ServiceError):
    status_code = 401


class AuthorizationError(ServiceError):
    status_code = 403


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class StoreMediaService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def initialize(self) -> None:
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.database.initialize()
        if self.database.user_count() == 0:
            username = self.settings.bootstrap_admin_username
            password = self.settings.bootstrap_admin_password
            if username and password:
                self.database.create_user(
                    user_id=str(uuid.uuid4()), username=username,
                    display_name=self.settings.bootstrap_admin_display_name,
                    password_hash=hash_password(password), role=Role.SYSTEM_ADMIN,
                    region_id=None, store_id=None,
                )

    def login(self, username: str, password: str) -> tuple[str, User, datetime]:
        credentials = self.database.get_user_credentials(username.strip())
        if not credentials or not credentials[0].is_active or not verify_password(password, credentials[1]):
            raise AuthenticationError("用户名或密码错误")
        user = credentials[0]
        token, digest = new_session_token()
        expires_at = datetime.now(UTC) + timedelta(hours=self.settings.session_hours)
        self.database.create_session(digest, user.id, expires_at)
        return token, user, expires_at

    def authenticate(self, token: str) -> User:
        user = self.database.user_for_session(token_digest(token))
        if not user:
            raise AuthenticationError("登录已失效，请重新登录")
        return user

    def logout(self, token: str) -> None:
        self.database.delete_session(token_digest(token))

    def create_store(self, actor: User, *, store_id: str, name: str, region_id: str) -> Store:
        if actor.role not in {Role.SYSTEM_ADMIN, Role.REGIONAL_MANAGER}:
            raise AuthorizationError("当前角色不能创建门店")
        store_id = store_id.strip()
        region_id = region_id.strip()
        if not _SCOPE_ID.fullmatch(store_id) or not _SCOPE_ID.fullmatch(region_id):
            raise ServiceError("门店和区域标识只能包含字母、数字、下划线或短横线")
        if actor.role is Role.REGIONAL_MANAGER and actor.region_id != region_id:
            raise AuthorizationError("区域经理只能在所属区域创建门店")
        try:
            return self.database.create_store(store_id=store_id, name=name.strip(), region_id=region_id)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("门店标识已存在") from exc

    def list_stores(self, actor: User) -> list[Store]:
        stores = self.database.list_stores()
        return [store for store in stores if can_access_store(actor, store)]

    def create_user(self, actor: User, *, username: str, password: str, display_name: str,
                    role: Role, region_id: str | None, store_id: str | None) -> User:
        if not can_manage_role(actor, role):
            raise AuthorizationError("当前角色不能分配该角色")
        region_id, store_id = self._validated_scope(actor, role, region_id, store_id)
        try:
            return self.database.create_user(
                user_id=str(uuid.uuid4()), username=username.strip(), display_name=display_name.strip(),
                password_hash=hash_password(password), role=role, region_id=region_id,
                store_id=store_id, created_by=actor.id,
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("用户名已存在") from exc

    def list_users(self, actor: User) -> list[User]:
        users = self.database.list_users()
        if actor.role is Role.SYSTEM_ADMIN:
            return users
        return [user for user in users if user.id == actor.id or can_manage_user(actor, user)]

    def update_user(self, actor: User, user_id: str, *, display_name: str, role: Role,
                    region_id: str | None, store_id: str | None, is_active: bool,
                    password: str | None) -> User:
        target = self.database.get_user(user_id)
        if not target:
            raise NotFoundError("用户不存在")
        if actor.id == target.id and (not is_active or role is not target.role):
            raise ServiceError("不能停用自己或修改自己的角色")
        if not can_manage_user(actor, target) or not can_manage_role(actor, role):
            raise AuthorizationError("无权修改该用户")
        region_id, store_id = self._validated_scope(actor, role, region_id, store_id)
        return self.database.update_user(
            user_id, display_name=display_name.strip(), role=role, region_id=region_id,
            store_id=store_id, is_active=is_active,
            password_hash=hash_password(password) if password else None,
        )

    def upload_media(self, actor: User, *, store_id: str, title: str,
                     original_name: str, source: BinaryIO,
                     image_duration_seconds: float | None,
                     is_published: bool) -> MediaItem:
        store = self._store_for_actor(actor, store_id, write=True)
        if actor.role is Role.STAFF:
            raise AuthorizationError("店员没有发布权限")
        source.seek(0)
        detected = detect_media(source.read(16))
        source.seek(0)
        duration = image_duration_seconds if detected.media_type is MediaType.IMAGE else None
        if detected.media_type is MediaType.IMAGE:
            duration = 8 if duration is None else duration
            if not 1 <= duration <= 3600:
                raise ServiceError("图片停留时长必须在 1 到 3600 秒之间")
        item_id = str(uuid.uuid4())
        storage_name = f"{item_id}{detected.extension}"
        destination = self.settings.uploads_dir / storage_name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        maximum = self.settings.max_upload_mb * 1024 * 1024
        written = 0
        try:
            with temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > maximum:
                        raise ServiceError(f"文件不能超过 {self.settings.max_upload_mb} MB")
                    output.write(chunk)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        now = datetime.now(UTC)
        item = MediaItem(
            id=item_id, store_id=store.id, title=title.strip() or Path(original_name).stem,
            media_type=detected.media_type, content_type=detected.content_type,
            original_name=Path(original_name).name, storage_name=storage_name,
            image_duration_seconds=duration, sort_order=0, is_published=is_published,
            created_by=actor.id, created_at=now, updated_at=now,
        )
        try:
            return self.database.create_media(item)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def list_media(self, actor: User, store_id: str) -> list[MediaItem]:
        self._store_for_actor(actor, store_id)
        return self.database.list_media(store_id)

    def update_media(self, actor: User, media_id: str, *, title: str,
                     image_duration_seconds: float | None, sort_order: int,
                     is_published: bool) -> MediaItem:
        item = self._media_for_actor(actor, media_id, write=True)
        duration = image_duration_seconds if item.media_type is MediaType.IMAGE else None
        if item.media_type is MediaType.IMAGE and (duration is None or not 1 <= duration <= 3600):
            raise ServiceError("图片停留时长必须在 1 到 3600 秒之间")
        return self.database.update_media(
            media_id, title=title.strip(), image_duration_seconds=duration,
            sort_order=sort_order, is_published=is_published,
        )

    def delete_media(self, actor: User, media_id: str) -> None:
        item = self._media_for_actor(actor, media_id, write=True)
        self.database.delete_media(media_id)
        (self.settings.uploads_dir / item.storage_name).unlink(missing_ok=True)

    def update_playlist(self, actor: User, store_id: str, *, updates: list[dict[str, object]],
                        delete_ids: list[str]) -> list[MediaItem]:
        self._store_for_actor(actor, store_id, write=True)
        if actor.role is Role.STAFF:
            raise AuthorizationError("店员没有发布权限")
        update_ids = [str(update["id"]) for update in updates]
        if len(set(update_ids)) != len(update_ids) or len(set(delete_ids)) != len(delete_ids):
            raise ServiceError("播放清单包含重复素材")
        if set(update_ids) & set(delete_ids):
            raise ServiceError("同一素材不能同时更新和删除")

        normalized: list[dict[str, object]] = []
        for update in updates:
            item = self.database.get_media(str(update["id"]))
            if not item or item.store_id != store_id:
                raise NotFoundError("播放清单包含不存在的素材")
            duration = update["image_duration_seconds"] if item.media_type is MediaType.IMAGE else None
            if item.media_type is MediaType.IMAGE and (
                duration is None or not 1 <= float(duration) <= 3600
            ):
                raise ServiceError("图片停留时长必须在 1 到 3600 秒之间")
            normalized.append({
                "id": item.id,
                "title": str(update["title"]).strip(),
                "image_duration_seconds": duration,
                "sort_order": int(update["sort_order"]),
                "is_published": bool(update["is_published"]),
            })

        deleted_items: list[MediaItem] = []
        for media_id in delete_ids:
            item = self.database.get_media(media_id)
            if not item or item.store_id != store_id:
                raise NotFoundError("待删除素材不存在")
            deleted_items.append(item)

        try:
            result = self.database.apply_playlist(
                store_id, updates=normalized, delete_ids=delete_ids
            )
        except KeyError as exc:
            raise ConflictError("播放清单已发生变化，请刷新后重试") from exc
        for item in deleted_items:
            (self.settings.uploads_dir / item.storage_name).unlink(missing_ok=True)
        return result

    def public_playlist(self, store_id: str) -> tuple[Store, list[MediaItem]]:
        store = self.database.get_store(store_id)
        if not store or not store.is_active:
            raise NotFoundError("门店不存在或已停用")
        return store, self.database.list_media(store_id, published_only=True)

    def public_media_path(self, media_id: str) -> tuple[MediaItem, Path]:
        item = self.database.get_media(media_id)
        if not item or not item.is_published:
            raise NotFoundError("媒体不存在或尚未发布")
        path = self.settings.uploads_dir / item.storage_name
        if not path.is_file():
            raise NotFoundError("媒体文件不存在")
        return item, path

    def _validated_scope(self, actor: User, role: Role, region_id: str | None,
                         store_id: str | None) -> tuple[str | None, str | None]:
        if role is Role.SYSTEM_ADMIN:
            return None, None
        if role is Role.REGIONAL_MANAGER:
            if not region_id or not _SCOPE_ID.fullmatch(region_id):
                raise ServiceError("区域经理必须设置有效的区域标识")
            return region_id, None
        if not store_id:
            raise ServiceError("店长和店员必须设置所属门店")
        store = self._store_for_actor(actor, store_id, write=True)
        return store.region_id, store.id

    def _store_for_actor(self, actor: User, store_id: str, *, write: bool = False) -> Store:
        store = self.database.get_store(store_id)
        if not store:
            raise NotFoundError("门店不存在")
        if not can_access_store(actor, store, write=write):
            raise AuthorizationError("无权访问该门店")
        return store

    def _media_for_actor(self, actor: User, media_id: str, *, write: bool = False) -> MediaItem:
        item = self.database.get_media(media_id)
        if not item:
            raise NotFoundError("媒体不存在")
        self._store_for_actor(actor, item.store_id, write=write)
        if write and actor.role is Role.STAFF:
            raise AuthorizationError("店员没有发布权限")
        return item
