"""验证执行器：在后台线程中复用 scripts/house_verify.py 的逻辑。

- 从产权证图片提取业务件号 / 证件编码（VL 模型）；
- 驱动浏览器完成蓉e办房源信息验证并截图；
- 全程把脚本自身的 print 与关键里程碑回传为进度事件。

执行在独立线程中运行同步 Playwright，避免阻塞事件循环。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from app.infrastructure.config.settings import Settings
from app.infrastructure.job_store import JobStore, SUCCEEDED, FAILED


class _StdoutTee:
    """临时替换 sys.stdout，按行转发为进度事件，同时保留原输出。"""

    def __init__(self, real, emit) -> None:
        self._real = real
        self._emit = emit
        self._buf = ""

    def write(self, data: str) -> int:  # noqa: D401
        if not data:
            return 0
        self._real.write(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._emit("log", line)
        return len(data)

    def flush(self) -> None:
        if self._buf.strip():
            self._emit("log", self._buf.strip())
        self._buf = ""
        self._real.flush()


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


def run_verification(job_id: str, cert_path: Path, settings: Settings,
                    store: JobStore) -> None:
    """执行完整验证流程并更新任务状态。在后台线程调用。"""
    emit = lambda t, m: store.append_event(job_id, t, m)

    def _emit_milestone(msg: str) -> None:
        emit("milestone", msg)

    work_dir = Path(store.require(job_id).work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    real_stdout = sys.stdout
    tee = _StdoutTee(real_stdout, emit)
    saved_stdout = sys.stdout

    try:
        sys.stdout = tee
        hv = _load_house_verify(_scripts_dir(settings))
        api_key = settings.openrouter_api_key
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY 未配置，无法调用视觉模型")

        _emit_milestone("正在提取证件字段…")
        cred = hv.extract_credentials(
            api_key, settings.vl_model, cert_path,
            fallback_model=settings.vl_model_fallback or None,
        )
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
        msg = str(exc) or exc.__class__.__name__
        emit("error", f"验证失败：{msg}")
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

    # 打包：全部图片 + extracted.json
    zip_path = work_dir / "all_artifacts.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for a in artifacts:
                zf.write(a["path"], a["filename"])
            ex = work_dir / "extracted.json"
            if ex.exists():
                zf.write(ex, "extracted.json")
        artifacts.append({
            "spec": "zip", "title": "全部产物打包", "filename": "all_artifacts.zip",
            "path": str(zip_path), "size": zip_path.stat().st_size,
            "content_type": "application/zip",
        })
    except Exception:  # noqa: BLE001 - 打包失败不影响已有截图下载
        pass
    return artifacts
