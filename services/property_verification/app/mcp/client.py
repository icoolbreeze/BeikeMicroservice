"""pv-mcp 的 HTTP 客户端：调用已部署的 property_verification 服务 API。

服务端接口契约见 services/property_verification/README.md（v1）。
客户端只做传输与错误翻译，不实现任何核验逻辑。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import requests

from app.security.file_validation import UploadValidationError, validate_upload

_SPECS = ("panel", "full", "zip")


class PVClientError(Exception):
    """上游调用失败（网络异常或业务错误），message 面向 agent 展示。"""


def _url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def _raise_for_error(resp: requests.Response, context: str) -> None:
    detail = ""
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("message") or ""
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    if isinstance(detail, dict):
        detail = detail.get("message") or str(detail)
    raise PVClientError(
        f"{context}失败（HTTP {resp.status_code}）：{detail or resp.reason}")


class PVClient:
    """property_verification 服务的轻量 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: float = 120) -> None:
        self.base_url = base_url
        self.timeout = timeout

    # ---- 提交核验 -------------------------------------------------------
    def submit(self, image_path: str, data: bytes | None = None,
               filename: str | None = None) -> dict:
        """上传证件图片创建核验任务，返回任务视图 dict。"""
        if data is None:
            path = Path(image_path)
            if not path.exists():
                raise PVClientError(f"图片文件不存在：{image_path}")
            data = path.read_bytes()
            filename = filename or path.name
        try:
            name = validate_upload(filename or "upload.jpg", data)
        except UploadValidationError as exc:
            raise PVClientError(str(exc)) from exc

        try:
            resp = requests.post(
                _url(self.base_url, "/api/v1/verification"),
                files={"file": (name, data,
                                "image/jpeg" if name.lower().endswith((".jpg", ".jpeg"))
                                else "image/png")},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code == 429:
            detail = ""
            try:
                detail = (resp.json().get("detail") or {}).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            raise PVClientError(f"请求过于频繁：{detail}，请稍后再试")
        if resp.status_code != 202:
            _raise_for_error(resp, "提交核验任务")
        data_view = (resp.json() or {}).get("data") or {}
        return {
            "job_id": data_view.get("job_id"),
            "status": data_view.get("status"),
            "queue_ahead": data_view.get("queue_ahead", 0),
            "remaining_minute": data_view.get("remaining_minute"),
            "remaining_day": data_view.get("remaining_day"),
            "served_count": data_view.get("served_count"),
        }

    # ---- 查询 -----------------------------------------------------------
    def get_status(self, job_id: str) -> dict:
        """任务状态快照（含错误与产物清单）。"""
        try:
            resp = requests.get(
                _url(self.base_url, f"/api/v1/jobs/{job_id}"),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code == 404:
            raise PVClientError("任务不存在或已过期")
        if resp.status_code != 200:
            _raise_for_error(resp, "查询任务状态")
        return (resp.json() or {}).get("data") or {}

    def get_artifacts(self, job_id: str) -> dict:
        """产物清单（status + artifacts）。"""
        try:
            resp = requests.get(
                _url(self.base_url, f"/api/v1/verification/{job_id}/artifacts"),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code == 404:
            raise PVClientError("任务不存在或已过期")
        if resp.status_code != 200:
            _raise_for_error(resp, "查询产物清单")
        return (resp.json() or {}).get("data") or {}

    def get_result(self, job_id: str) -> dict:
        """核验结果（原始数据 + 产物清单，图片链接为绝对 URL）。"""
        try:
            resp = requests.get(
                _url(self.base_url, f"/api/v1/verification/{job_id}/result"),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code == 404:
            raise PVClientError("任务不存在或已过期")
        if resp.status_code != 200:
            _raise_for_error(resp, "查询核验结果")
        data = (resp.json() or {}).get("data") or {}

        # 合并产物清单并把图片下载链接补全为绝对 URL（job_id 凭据访问）
        artifacts: list[dict] = []
        try:
            artifacts = self.get_artifacts(job_id).get("artifacts") or []
        except PVClientError:
            pass
        for artifact in artifacts:
            if artifact.get("url"):
                artifact["url"] = f"{self.base_url.rstrip('/')}{artifact['url']}"
        data["artifacts"] = artifacts
        return data

    def download(self, job_id: str, spec: str, dest_dir: str | None = None) -> str:
        """下载指定规格产物到本地，返回绝对路径。"""
        if spec not in _SPECS:
            raise PVClientError(
                f"未知产物规格 {spec!r}，可选：{' / '.join(_SPECS)}")
        try:
            resp = requests.get(
                _url(self.base_url,
                     f"/api/v1/verification/{job_id}/download/{spec}"),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code in (404, 410):
            raise PVClientError("产物不存在或已失效")
        if resp.status_code != 200:
            _raise_for_error(resp, "下载产物")

        content_type = resp.headers.get("content-type", "")
        ext = ".png" if "png" in content_type else (
            ".zip" if "zip" in content_type else ".bin")
        directory = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"pv_{job_id}_{spec}{ext}"
        target.write_bytes(resp.content)
        return str(target)

    def get_share_link(self, job_id: str, spec: str, ttl: int = 600) -> dict:
        """生成短期签名下载链接（供转发给最终用户），返回绝对 URL 与过期时间。"""
        try:
            resp = requests.get(
                _url(self.base_url,
                     f"/api/v1/verification/{job_id}/share/{spec}"),
                params={"ttl": ttl},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code == 404:
            raise PVClientError("任务不存在或产物不存在")
        if resp.status_code != 200:
            _raise_for_error(resp, "生成分享链接")
        data = (resp.json() or {}).get("data") or {}
        if data.get("url"):
            data["url"] = f"{self.base_url.rstrip('/')}{data['url']}"
        return data

    def stats(self) -> dict:
        """服务累计受理次数。"""
        try:
            resp = requests.get(
                _url(self.base_url, "/api/v1/verification/stats"),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise PVClientError(f"无法连接核验服务：{exc}") from exc
        if resp.status_code != 200:
            _raise_for_error(resp, "查询服务统计")
        return (resp.json() or {}).get("data") or {}
