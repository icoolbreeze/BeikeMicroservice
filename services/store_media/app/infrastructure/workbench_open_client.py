"""把核分页的「打开贝壳」转给 crm_connector，由它注入凭证后打开工作台。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_LISTING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,31}$")
_CODE_MESSAGES = {
    "CRM_AUTH_REQUIRED": "CRM 未登录，请先在连接器扫码",
    "CRM_UPSTREAM_NOT_CONFIGURED": "CRM 连接器未配置",
    "CRM_UPSTREAM_INVALID_INPUT": "房源编号无效",
    "CRM_CONNECTOR_ERROR": "凭证浏览器暂时不可用",
}


class WorkbenchOpenError(Exception):
    def __init__(self, message: str, *, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class WorkbenchOpenClient:
    def __init__(
        self,
        crm_base_url: str,
        *,
        request_timeout: float = 60.0,
        user_agent: str = "store-media-workbench-open/1.0",
    ) -> None:
        self._base_url = crm_base_url.rstrip("/")
        self._timeout = request_timeout
        self._user_agent = user_agent

    def open_rental_listing(self, listing_id: str) -> dict[str, Any]:
        safe_id = (listing_id or "").strip()
        if not _LISTING_ID_RE.fullmatch(safe_id):
            raise WorkbenchOpenError("房源编号无效", status_code=400)
        path = f"/api/v1/workbench/rental/{quote(safe_id, safe='')}/open"
        request = Request(
            f"{self._base_url}{path}",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "User-Agent": self._user_agent,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise WorkbenchOpenError(
                _message_from_http_error(exc),
                status_code=_safe_status(exc.code),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WorkbenchOpenError("无法连接 CRM 连接器，凭证浏览器未启动") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise WorkbenchOpenError("CRM 连接器返回了无法解析的响应") from exc
        if not isinstance(payload, dict):
            raise WorkbenchOpenError("CRM 连接器返回了无法解析的响应")
        return payload


def _safe_status(code: int | None) -> int:
    if code in {400, 401, 404, 409, 501, 502, 503}:
        return code
    if code == 403:
        return 401
    return 503


def _message_from_http_error(exc: HTTPError) -> str:
    raw = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    payload: Any = None
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
    code = _error_code(payload)
    if code in _CODE_MESSAGES:
        return _CODE_MESSAGES[code]
    if exc.code == 401:
        return _CODE_MESSAGES["CRM_AUTH_REQUIRED"]
    if exc.code == 400:
        return _CODE_MESSAGES["CRM_UPSTREAM_INVALID_INPUT"]
    text = _detail_text(payload)
    return text or "打开工作台失败"


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            return detail["code"]
        if isinstance(payload.get("code"), str):
            return payload["code"]
    return ""


def _detail_text(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"]
        if isinstance(detail, str):
            return detail
        if isinstance(payload.get("message"), str):
            return payload["message"]
    if isinstance(payload, str):
        return payload
    return ""
