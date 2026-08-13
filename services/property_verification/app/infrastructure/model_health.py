"""视觉模型健康注册表与探活。

每个模型以 ``channel:model`` 为键登记状态；不可用的模型在提取任务链中
被直接跳过，避免 30s×3 次重试浪费时间。探活线程每小时探一次；任务运行期
失败也会即时标记，直到下一次探活恢复。

探活请求为最小文本请求（max_tokens=4），不计入证件识别的语义调用。
"""

from __future__ import annotations

import threading
import time

import requests

from app.infrastructure.config.settings import Settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
PROBE_TIMEOUT_SECONDS = 15


class ModelHealthRegistry:
    """进程内模型健康状态（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, dict] = {}

    def mark_ok(self, key: str) -> None:
        with self._lock:
            self._status[key] = {"ok": True, "reason": "", "checked_at": time.time()}

    def mark_unavailable(self, key: str, reason: str) -> None:
        with self._lock:
            self._status[key] = {"ok": False, "reason": str(reason)[:200],
                                 "checked_at": time.time()}

    def is_available(self, key: str) -> bool:
        with self._lock:
            status = self._status.get(key)
            return True if status is None else bool(status["ok"])

    def status(self, key: str) -> dict | None:
        with self._lock:
            status = self._status.get(key)
            return dict(status) if status else None


def build_model_entries(settings: Settings) -> list[dict]:
    """按配置构建模型条目（供提取任务与探活共用）。

    返回：[{key, label, api_key, model, channel}]，key 全局唯一。
    """
    entries: list[dict] = []
    if settings.openrouter_api_key:
        entries.append({
            "key": "openrouter", "label": "主识别服务(OpenRouter)",
            "api_key": settings.openrouter_api_key,
            "model": settings.vl_model, "channel": "openrouter",
        })
    if settings.nvidia_api_key:
        entries.append({
            "key": "nvidia", "label": "备用识别服务(NVIDIA)",
            "api_key": settings.nvidia_api_key,
            "model": settings.vl_model_fallback, "channel": "nvidia",
        })
        if settings.vl_model_fallback2:
            entries.append({
                "key": "nvidia2", "label": "备用识别服务2(StepFun)",
                "api_key": settings.nvidia_api_key,
                "model": settings.vl_model_fallback2, "channel": "nvidia",
            })
    return entries


def probe_model(api_key: str, model: str, channel: str,
                timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """对单个模型发起最小探活请求，返回 (是否可用, 失败原因)。"""
    url = NVIDIA_URL if channel == "nvidia" else OPENROUTER_URL
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 4,
                  "messages": [{"role": "user", "content": "ping"}]},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 网络异常统一视为不可用
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    if resp.status_code in (200, 201):
        return True, ""
    return False, f"HTTP {resp.status_code}: {resp.text[:120]}"


def probe_entry(entry: dict, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """对模型条目探活；请求网络层挂死时在硬性时限内返回不可用。"""
    box: dict = {}

    def _run() -> None:
        box["result"] = probe_model(entry["api_key"], entry["model"],
                                    entry["channel"], timeout)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=max(timeout * 2, 10.0))
    if "result" not in box:
        return False, "探活超时"
    return box["result"]
