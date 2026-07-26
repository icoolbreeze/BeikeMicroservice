"""上传文件安全校验：类型嗅探、大小限制、文件名规范化。"""
from __future__ import annotations

from pathlib import Path

# 文件头魔数 -> MIME
_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
]
_ALLOWED_EXT = {".jpg", ".jpeg", ".png"}
_MAX_NAME_LEN = 128


class UploadValidationError(Exception):
    """上传校验未通过。"""


def sniff_mime(data: bytes) -> str | None:
    """根据文件头判断图片类型。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    return None


def validate_upload(filename: str, data: bytes, max_mb: int = 10) -> str:
    """校验上传文件，返回规范化后的文件名；不通过抛 UploadValidationError。

    - 类型仅允许 JPEG / PNG；
    - 大小不超过 ``max_mb``；
    - 文件名去路径、保留扩展名、截断长度。
    """
    if not data:
        raise UploadValidationError("文件内容为空")
    if len(data) > max_mb * 1024 * 1024:
        raise UploadValidationError(f"文件超过 {max_mb}MB 限制")

    mime = sniff_mime(data)
    if mime is None:
        raise UploadValidationError("仅支持 JPEG / PNG 格式图片")

    ext = ".jpg" if mime == "image/jpeg" else ".png"
    name = Path(filename).name or f"upload{ext}"
    if not name.lower().endswith((".jpg", ".jpeg", ".png")):
        name = Path(name).stem[:64] + ext
    return name[:_MAX_NAME_LEN]
