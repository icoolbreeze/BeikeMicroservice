"""文件存储抽象（占位）。"""
from abc import ABC, abstractmethod


class StorageProvider(ABC):
    """上传文件与结果截图的存储能力抽象。"""

    @abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """保存文件并返回引用（占位）。"""
        raise NotImplementedError

    @abstractmethod
    def load(self, key: str) -> bytes:
        """读取文件（占位）。"""
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除文件（占位）。"""
        raise NotImplementedError
