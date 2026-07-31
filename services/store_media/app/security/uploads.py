from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import MediaType


@dataclass(frozen=True)
class DetectedMedia:
    media_type: MediaType
    content_type: str
    extension: str


def detect_media(data: bytes) -> DetectedMedia:
    if data.startswith(b"\xff\xd8\xff"):
        return DetectedMedia(MediaType.IMAGE, "image/jpeg", ".jpg")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedMedia(MediaType.IMAGE, "image/png", ".png")
    if data.startswith((b"GIF87a", b"GIF89a")):
        return DetectedMedia(MediaType.IMAGE, "image/gif", ".gif")
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return DetectedMedia(MediaType.IMAGE, "image/webp", ".webp")
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] == b"qt  ":
        return DetectedMedia(MediaType.VIDEO, "video/quicktime", ".mov")
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return DetectedMedia(MediaType.VIDEO, "video/mp4", ".mp4")
    if data.startswith(b"\x1aE\xdf\xa3"):
        return DetectedMedia(MediaType.VIDEO, "video/webm", ".webm")
    if data.startswith(b"OggS"):
        return DetectedMedia(MediaType.VIDEO, "video/ogg", ".ogv")
    raise ValueError("仅支持 JPEG/PNG/GIF/WebP 图片或 MP4/WebM/Ogg 视频")
