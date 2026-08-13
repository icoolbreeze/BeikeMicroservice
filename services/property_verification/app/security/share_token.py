"""产物下载链接的短期签名（HMAC-SHA256）。

场景：MCP / Hermes 需要把截图链接转发给最终用户时，用短期签名链接代替
直接暴露 job_id 凭据的原始下载地址。签名包含 job_id、spec 与过期时间，
过期或篡改即失效；不带 token 的原始访问方式保持不变（本地工具拉取）。
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

_process_secret: str | None = None


def process_secret(configured: str) -> str:
    """进程级签名密钥：优先用配置值，否则进程启动时随机生成（重启即失效）。"""
    global _process_secret
    if configured:
        return configured
    if _process_secret is None:
        _process_secret = uuid.uuid4().hex
    return _process_secret


def sign_share_token(job_id: str, spec: str, expires_at: int,
                     secret: str) -> str:
    """生成 ``{expires_at}.{mac}`` 格式的短期签名。"""
    payload = f"{job_id}|{spec}|{int(expires_at)}"
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                   hashlib.sha256).hexdigest()[:16]
    return f"{int(expires_at)}.{mac}"


def verify_share_token(job_id: str, spec: str, token: str, secret: str,
                       now: float | None = None) -> bool:
    """校验签名是否有效且未过期。"""
    if not token:
        return False
    try:
        expires_raw, _mac = token.split(".", 1)
        expires_at = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    current = time.time() if now is None else now
    if expires_at < current:
        return False
    expected = sign_share_token(job_id, spec, expires_at, secret)
    return hmac.compare_digest(expected, token)
