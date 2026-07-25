"""本地文件存储（占位）。

实现 domain.providers.storage_provider.StorageProvider；
文件仅写入服务自身 storage/ 目录。
"""
from __future__ import annotations


class LocalStorage:
    """本地磁盘存储适配（当前不实现）。

    TODO: 注入基础目录，实现 save/load/delete，防止路径穿越。
    """

    def save(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def load(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError
