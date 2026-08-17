"""模型健康注册表、探活与提取链跳过的单元测试。"""
from __future__ import annotations

import time

from app.infrastructure.config.settings import Settings
from app.infrastructure.model_health import (ModelHealthRegistry,
                                             build_model_entries,
                                             probe_entry, probe_local_ocr,
                                             probe_model)
from app.infrastructure.verification_runner import _filter_available


def _full_settings() -> Settings:
    return Settings(
        openrouter_api_key="or-key",
        nvidia_api_key="nv-key",
        vl_model="nvidia/nemotron-nano-12b-v2-vl:free",
        vl_model_fallback="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        vl_model_fallback2="stepfun-ai/step-3.7-flash",
    )


def test_registry_defaults_to_available():
    health = ModelHealthRegistry()
    assert health.is_available("unknown-key") is True


def test_registry_mark_unavailable_and_ok():
    health = ModelHealthRegistry()
    health.mark_unavailable("openrouter", "HTTP 504")
    assert health.is_available("openrouter") is False
    assert health.status("openrouter")["reason"] == "HTTP 504"
    health.mark_ok("openrouter")
    assert health.is_available("openrouter") is True


def test_build_model_entries_order_and_keys():
    entries = build_model_entries(_full_settings())
    assert [e["key"] for e in entries] == ["openrouter", "nvidia", "nvidia2"]
    assert [e["channel"] for e in entries] == ["openrouter", "nvidia", "nvidia"]


def test_build_model_entries_without_nvidia():
    settings = Settings(openrouter_api_key="or-key")
    entries = build_model_entries(settings)
    assert [e["key"] for e in entries] == ["openrouter"]


def test_build_model_entries_with_local_ocr_first():
    settings = Settings(openrouter_api_key="or-key", nvidia_api_key="nv-key",
                        local_ocr_url="http://127.0.0.1:8081/v1")
    entries = build_model_entries(settings)
    assert [e["key"] for e in entries] == ["llama", "openrouter", "nvidia", "nvidia2"]
    assert entries[0]["base_url"] == "http://127.0.0.1:8081/v1"
    assert entries[0]["channel"] == "llama"


def test_build_model_entries_fallback2_disabled():
    settings = Settings(openrouter_api_key="or-key", nvidia_api_key="nv-key",
                        vl_model_fallback2="")
    entries = build_model_entries(settings)
    assert [e["key"] for e in entries] == ["openrouter", "nvidia"]


def test_filter_available_skips_unavailable():
    health = ModelHealthRegistry()
    health.mark_unavailable("nvidia", "DEGRADED")
    tasks = [{"key": "openrouter", "label": "A"},
             {"key": "nvidia", "label": "B"}]
    available, skipped = _filter_available(tasks, health)
    assert [t["key"] for t in available] == ["openrouter"]
    assert [t["key"] for t in skipped] == ["nvidia"]


def test_filter_available_without_health():
    tasks = [{"key": "openrouter", "label": "A"}]
    available, skipped = _filter_available(tasks, None)
    assert len(available) == 1 and skipped == []


def test_probe_model_http_error(monkeypatch):
    class _Resp:
        status_code = 400
        text = "DEGRADED function cannot be invoked"

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.post",
        lambda *a, **k: _Resp())
    ok, reason = probe_model("key", "m", "nvidia")
    assert ok is False and "DEGRADED" in reason


def test_probe_model_ok(monkeypatch):
    class _Resp:
        status_code = 200
        text = ""

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.post",
        lambda *a, **k: _Resp())
    ok, reason = probe_model("key", "m", "openrouter")
    assert ok is True and reason == ""


def test_probe_model_network_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("timeout")

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.post", _boom)
    ok, reason = probe_model("key", "m", "nvidia")
    assert ok is False and "OSError" in reason


def test_probe_local_ocr_ok(monkeypatch):
    class _Resp:
        status_code = 200
        text = "{\"status\":\"ok\"}"

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.get",
        lambda *a, **k: _Resp())
    ok, reason = probe_local_ocr("http://127.0.0.1:8081/v1")
    assert ok is True and reason == ""


def test_probe_local_ocr_strips_v1_for_health(monkeypatch):
    """llama-server 的 /health 挂在根路径，base_url 的 /v1 前缀需去掉。"""
    seen = {}

    class _Resp:
        status_code = 200
        text = "ok"

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.get", fake_get)
    ok, _ = probe_local_ocr("http://127.0.0.1:8081/v1")
    assert ok is True
    assert seen["url"] == "http://127.0.0.1:8081/health"


def test_probe_local_ocr_root_404_falls_back_to_v1(monkeypatch):
    """根路径探活 404 时，兜底尝试 base_url 直拼的 /v1/health。"""
    seen = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = str(code)

    def fake_get(url, **kwargs):
        seen.append(url)
        return _Resp(200 if "/v1/health" in url else 404)

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.get", fake_get)
    ok, _ = probe_local_ocr("http://127.0.0.1:8081/v1")
    assert ok is True
    assert seen == ["http://127.0.0.1:8081/health",
                    "http://127.0.0.1:8081/v1/health"]


def test_probe_local_ocr_error(monkeypatch):
    class _Resp:
        status_code = 500
        text = "bad"

    monkeypatch.setattr(
        "app.infrastructure.model_health.requests.get",
        lambda *a, **k: _Resp())
    ok, reason = probe_local_ocr("http://127.0.0.1:8081/v1")
    assert ok is False and "HTTP 500" in reason


def test_probe_entry_hard_deadline(monkeypatch):
    """网络层挂死时 probe_entry 在硬性时限内返回不可用。"""
    def _hang(*a, **k):
        time.sleep(120)

    monkeypatch.setattr(
        "app.infrastructure.model_health.probe_model", _hang)
    entry = {"api_key": "k", "model": "m", "channel": "nvidia"}
    started = time.monotonic()
    ok, reason = probe_entry(entry, timeout=0.5)
    elapsed = time.monotonic() - started
    assert ok is False and reason == "探活超时"
    assert elapsed < 12  # join 时限下限为 10s
