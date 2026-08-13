"""pv-mcp 客户端、限流与服务装配的单元测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.infrastructure.config.settings import Settings
from app.mcp.client import PVClient, PVClientError
from app.mcp.rate_limit import RateLimiter
from app.mcp.schemas import VerifySubmitInput
from app.mcp.server import build_pv_mcp_server


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None,
                 content: bytes = b"", reason: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = content
        self.reason = reason
        self.text = content.decode("utf-8", errors="replace")

    def json(self):
        return self._payload


def _jpeg() -> bytes:
    import io
    from PIL import Image
    image = Image.new("RGB", (4, 4), "white")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def test_submit_success(monkeypatch):
    captured = {}

    def fake_post(url, files=None, timeout=None):
        captured["url"] = url
        captured["files"] = files
        return _FakeResponse(202, {"code": 0, "message": "ok", "data": {
            "job_id": "abc", "status": "pending", "queue_ahead": 0,
            "remaining_minute": 1, "remaining_day": 29, "served_count": 33}})

    monkeypatch.setattr("requests.post", fake_post)
    client = PVClient("http://127.0.0.1:8000")
    result = client.submit("cert.jpg", data=_jpeg(), filename="cert.jpg")
    assert result["job_id"] == "abc"
    assert captured["url"] == "http://127.0.0.1:8000/api/v1/verification"
    assert "file" in captured["files"]


def test_submit_missing_file():
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="文件不存在"):
        client.submit("C:/nonexistent/cert.jpg")


def test_submit_rate_limited(monkeypatch):
    def fake_post(url, files=None, timeout=None):
        return _FakeResponse(429, {"detail": {"message": "已达限流"}})

    monkeypatch.setattr("requests.post", fake_post)
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="过于频繁"):
        client.submit("cert.jpg", data=_jpeg(), filename="cert.jpg")


def test_submit_invalid_upload():
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="仅支持 JPEG / PNG"):
        client.submit("cert.gif", data=b"GIF89a", filename="cert.gif")


def test_get_status(monkeypatch):
    def fake_get(url, timeout=None):
        assert url.endswith("/api/v1/jobs/abc")
        return _FakeResponse(200, {"code": 0, "message": "ok", "data": {
            "job_id": "abc", "status": "succeeded", "error": None,
            "artifacts": []}})

    monkeypatch.setattr("requests.get", fake_get)
    client = PVClient("http://127.0.0.1:8000")
    status = client.get_status("abc")
    assert status["status"] == "succeeded"


def test_get_status_not_found(monkeypatch):
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: _FakeResponse(404, {}))
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="不存在"):
        client.get_status("abc")


def test_get_result(monkeypatch):
    def fake_get(url, timeout=None):
        if url.endswith("/api/v1/verification/abc/result"):
            return _FakeResponse(200, {"code": 0, "message": "ok", "data": {
                "status": "succeeded",
                "result": {"headers": ["是否抵押", "是否查封"],
                           "row": ["抵押", "否"],
                           "fields": {"是否抵押": "抵押", "是否查封": "否"}}}})
        if url.endswith("/api/v1/verification/abc/artifacts"):
            return _FakeResponse(200, {"code": 0, "message": "ok", "data": {
                "status": "succeeded",
                "artifacts": [{"spec": "panel", "title": "区域截图",
                               "filename": "result_panel.png", "size": 1,
                               "content_type": "image/png",
                               "url": "/api/v1/verification/abc/download/panel"}]}})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("requests.get", fake_get)
    client = PVClient("http://119.29.187.184:8000")
    data = client.get_result("abc")
    assert data["result"]["fields"]["是否查封"] == "否"
    assert "结论" not in data["result"]
    assert data["artifacts"][0]["url"] == (
        "http://119.29.187.184:8000/api/v1/verification/abc/download/panel")


def test_get_result_not_found(monkeypatch):
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: _FakeResponse(404, {}))
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="不存在"):
        client.get_result("abc")


def test_get_share_link_absolute(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert "/share/panel" in url
        assert params == {"ttl": 300}
        return _FakeResponse(200, {"code": 0, "message": "ok", "data": {
            "url": "/api/v1/verification/abc/download/panel?token=t.e",
            "expires_at": 123456, "ttl_seconds": 300}})

    monkeypatch.setattr("requests.get", fake_get)
    client = PVClient("http://119.29.187.184:8000")
    data = client.get_share_link("abc", "panel", ttl=300)
    assert data["url"] == ("http://119.29.187.184:8000"
                           "/api/v1/verification/abc/download/panel?token=t.e")


def test_get_share_link_not_found(monkeypatch):
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: _FakeResponse(404, {}))
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="不存在"):
        client.get_share_link("abc", "panel")


def test_download_saves_file(monkeypatch, tmp_path):
    def fake_get(url, timeout=None):
        assert "/download/panel" in url
        return _FakeResponse(200, headers={"content-type": "image/png"},
                             content=b"\x89PNG-fake")

    monkeypatch.setattr("requests.get", fake_get)
    client = PVClient("http://127.0.0.1:8000")
    path = client.download("abc", "panel", dest_dir=str(tmp_path))
    assert Path(path).exists()
    assert Path(path).read_bytes() == b"\x89PNG-fake"
    assert path.endswith(".png")


def test_download_bad_spec():
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="未知产物规格"):
        client.download("abc", "nope")


def test_download_gone(monkeypatch):
    monkeypatch.setattr("requests.get",
                        lambda *a, **k: _FakeResponse(410, {}))
    client = PVClient("http://127.0.0.1:8000")
    with pytest.raises(PVClientError, match="已失效"):
        client.download("abc", "panel")


def test_rate_limiter_enforces():
    limiter = RateLimiter(2)
    assert limiter.allow("user") is True
    assert limiter.allow("user") is True
    assert limiter.allow("user") is False
    assert limiter.allow("other") is True


def test_rate_limiter_two_keys():
    limiter = RateLimiter(2)
    assert limiter.allow("a") and limiter.allow("a")
    assert limiter.allow("b") and limiter.allow("b")


class _FakeClient:
    def submit(self, image_path):
        return {"job_id": "abc", "status": "pending"}

    def get_status(self, job_id):
        return {"job_id": job_id, "status": "succeeded"}

    def get_artifacts(self, job_id):
        return {"status": "succeeded", "artifacts": []}

    def get_result(self, job_id):
        return {"status": "succeeded",
                "result": {"headers": ["是否查封"], "row": ["否"],
                           "fields": {"是否查封": "否"}},
                "artifacts": []}

    def get_share_link(self, job_id, spec, ttl=600):
        return {"url": "http://x/api/v1/verification/abc/download/panel?token=t",
                "expires_at": 123456}

    def download(self, job_id, spec, dest_dir=None):
        return "/tmp/pv_abc_panel.png"

    def stats(self):
        return {"served_count": 1}


def test_server_registers_tools():
    server = build_pv_mcp_server(_FakeClient(), Settings())
    names = sorted(server._tool_manager._tools.keys())
    assert names == [
        "pv_verify_artifacts", "pv_verify_download", "pv_verify_result",
        "pv_verify_share_link", "pv_verify_stats", "pv_verify_status",
        "pv_verify_submit",
    ]


def test_server_rejects_unknown_input_fields():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VerifySubmitInput.model_validate(
            {"image_path": "a.jpg", "nonsense": 1})
