"""验证执行器：在后台线程中复用 scripts/house_verify.py 的逻辑。

- 从产权证图片提取业务件号 / 证件编码（VL 模型）；
- 驱动浏览器完成蓉e办房源信息验证并截图；
- 全程把脚本自身的 print 与关键里程碑回传为进度事件。

执行在独立线程中运行同步 Playwright，避免阻塞事件循环。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.infrastructure.config.settings import Settings
from app.infrastructure.job_store import JobStore, SUCCEEDED, FAILED
from app.infrastructure.model_health import (ModelHealthRegistry,
                                             build_model_entries)


class _StdoutTee:
    """临时替换 sys.stdout，按行脱敏后转发为进度事件及控制台日志。"""

    def __init__(self, real, emit, sanitize) -> None:
        self._real = real
        self._emit = emit
        self._sanitize = sanitize
        self._buf = ""

    def write(self, data: str) -> int:  # noqa: D401
        if not data:
            return 0
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = self._sanitize(line.strip())
            if line:
                self._real.write(line + "\n")
                self._emit("log", line)
        return len(data)

    def flush(self) -> None:
        line = self._sanitize(self._buf.strip())
        if line:
            self._real.write(line)
            self._emit("log", line)
        self._buf = ""
        self._real.flush()


def _public_message(message: str, settings: Settings) -> str:
    """删除面向用户与控制台日志中的模型标识及其调用细节。"""
    result = str(message)
    for model in (settings.vl_model, settings.vl_model_fallback,
                  settings.vl_model_fallback2):
        if model:
            result = result.replace(model, "识别服务")
    # 兜底覆盖第三方返回的「model: provider/name」等形式，避免新增配置漏网。
    result = re.sub(
        r"(?i)(?:使用)?(?:模型|model)\s*[：:]\s*[^，,;；\s)）]+",
        "识别服务",
        result,
    )
    return result


def _scripts_dir(settings: Settings) -> Path:
    """定位仓库 scripts 目录（复用已有验证脚本）。"""
    if settings.scripts_dir:
        p = Path(settings.scripts_dir)
    else:
        # app/infrastructure/verification_runner.py -> repo root = parents[4]
        p = Path(__file__).resolve().parents[4] / "scripts"
    if not p.exists():
        raise FileNotFoundError(f"scripts 目录不存在：{p}")
    return p


def _load_house_verify(scripts_dir: Path):
    """把 scripts/ 加入 sys.path 并导入 house_verify 模块。"""
    s = str(scripts_dir)
    if s not in sys.path:
        sys.path.insert(0, s)
    import house_verify  # noqa: WPS433 - 运行时按需导入
    return house_verify


def _filter_available(tasks: list[dict],
                      health: ModelHealthRegistry | None) -> tuple[list[dict],
                                                                    list[dict]]:
    """过滤掉被健康注册表标记为不可用的模型，返回 (可用任务, 被跳过任务)。"""
    if health is None:
        return list(tasks), []
    available = [task for task in tasks if health.is_available(task["key"])]
    skipped = [task for task in tasks if not health.is_available(task["key"])]
    return available, skipped


def run_verification(job_id: str, cert_path: Path, settings: Settings,
                     store: JobStore, before_openrouter=None, before_nvidia=None,
                     health: ModelHealthRegistry | None = None) -> None:
    """执行完整验证流程并更新任务状态。在后台线程调用。"""
    def _emit(etype: str, message: str) -> None:
        store.append_event(job_id, etype, _public_message(message, settings))

    def _emit_milestone(msg: str) -> None:
        _emit("milestone", msg)

    work_dir = Path(store.require(job_id).work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    real_stdout = sys.stdout
    tee = _StdoutTee(real_stdout, _emit, lambda m: _public_message(m, settings))
    saved_stdout = sys.stdout

    try:
        sys.stdout = tee
        hv = _load_house_verify(_scripts_dir(settings))

        entries = build_model_entries(settings)
        if not entries:
            raise RuntimeError(
                "未配置任何视觉模型 API Key（OPENROUTER_API_KEY / NVIDIA_API_KEY）")

        prompts = {
            "openrouter": hv.EXTRACT_PROMPT,
            "nvidia": hv.OMNI_PROMPT,
            "nvidia2": hv.EXTRACT_PROMPT,
        }
        tasks: list[dict] = []
        for entry in entries:
            key = entry["key"]
            tasks.append({
                **entry,
                "prompt": prompts.get(key, hv.EXTRACT_PROMPT),
                "before_request": (before_nvidia if entry["channel"] == "nvidia"
                                   else before_openrouter),
            })
            if health is not None:
                tasks[-1]["on_success"] = (
                    lambda k=key: health.mark_ok(k))
                tasks[-1]["on_failure"] = (
                    lambda exc, k=key: health.mark_unavailable(k, str(exc)))

        available, skipped = _filter_available(tasks, health)
        for task in skipped:
            reason = ""
            status = health.status(task["key"]) if health else None
            if status:
                reason = f"（{status['reason']}）"
            _emit_milestone(f"{task['label']} 当前不可用，已跳过{reason}")
        if not available:
            raise RuntimeError("所有识别服务当前均不可用，请稍后再试")

        _emit_milestone("正在识别证件图片，提取关键字段…")
        result = hv.extract_credentials_chained(
            available, cert_path, retries=settings.vl_retries,
            timeout=settings.vl_timeout)
        cred = result["cred"]
        (work_dir / "extracted.json").write_text(
            json.dumps(cred, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit_milestone(
            f"提取完成：业务件号={cred['业务件号']} 证件编码={cred['证件编码']}")

        _emit_milestone("正在调用蓉e办房源信息验证…")
        outputs = hv.run_query(
            cred["业务件号"], cred["证件编码"],
            settings.verification_channel_url, work_dir, headed=False)

        artifacts = _collect_artifacts(work_dir, outputs)
        store.set_artifacts(job_id, artifacts)
        _emit_milestone("验证完成，截图已生成")
        store.append_event(job_id, "done", "done")
        store.finish(job_id, SUCCEEDED)
    except BaseException as exc:  # noqa: BLE001 - 捕获 SystemExit 与异常
        msg = _public_message(str(exc) or exc.__class__.__name__, settings)
        _emit("error", f"验证失败：{msg}")
        store.finish(job_id, FAILED, error=msg)
    finally:
        sys.stdout = saved_stdout
        try:
            tee.flush()
        except Exception:  # noqa: BLE001
            pass


def _collect_artifacts(work_dir: Path, outputs: dict) -> list[dict]:
    """整理可下载的产物清单（不同规格截图 + 打包 zip）。"""
    import zipfile

    specs = [
        ("panel", "区域截图", "result_panel.png", "image/png"),
        ("full", "整页截图", "result_full.png", "image/png"),
    ]
    artifacts: list[dict] = []
    for spec, title, name, ctype in specs:
        p = work_dir / name
        if p.exists():
            artifacts.append({
                "spec": spec, "title": title, "filename": name,
                "path": str(p), "size": p.stat().st_size,
                "content_type": ctype,
            })

    # 打包：全部图片 + extracted.json + result.json
    zip_path = work_dir / "all_artifacts.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for a in artifacts:
                zf.write(a["path"], a["filename"])
            for extra in ("extracted.json", "result.json"):
                p = work_dir / extra
                if p.exists():
                    zf.write(p, extra)
        artifacts.append({
            "spec": "zip", "title": "全部产物打包", "filename": "all_artifacts.zip",
            "path": str(zip_path), "size": zip_path.stat().st_size,
            "content_type": "application/zip",
        })
    except Exception:  # noqa: BLE001 - 打包失败不影响已有截图下载
        pass
    return artifacts
