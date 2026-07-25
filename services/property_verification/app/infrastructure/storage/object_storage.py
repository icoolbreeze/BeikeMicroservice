"""对象存储（占位）。

实现 domain.providers.storage_provider.StorageProvider；
接入对象与凭据全部来自配置注入，当前不实现。
"""
from __future__ import annotations


class ObjectStorage:
    """对象存储适配（当前不实现）。"""

    def save(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def load(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError
