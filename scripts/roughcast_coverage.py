"""离线复算清水房三个覆盖率（第 3 期,零上游请求）。

对照第 2 期修正前(legacy)与修正后(corrected)两套口径,用来定稿 2.2 阶梯。

    python scripts/roughcast_coverage.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE_MEDIA = ROOT / "services" / "store_media"
sys.path.insert(0, str(STORE_MEDIA))

from app.application.roughcast_coverage import (  # noqa: E402
    compute_coverage,
    load_references_by_community,
    load_targets,
    render_coverage,
)
from app.domain.roughcast_benchmark import CORRECTED, LEGACY  # noqa: E402
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.roughcast_repository import RoughcastRepository  # noqa: E402
from app.infrastructure.settings import load_settings  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    if not settings.database_path.exists():
        print(f"数据库不存在:{settings.database_path}")
        return 1
    repository = RoughcastRepository(Database(settings.database_path))
    targets = load_targets(repository)
    references = load_references_by_community(repository)
    print(f"数据库 : {settings.database_path}")
    print(f"载入   : P {len(targets)} 套 / 有快照小区 {len(references)}")
    print()
    legacy = compute_coverage(
        targets, references, config=LEGACY, config_name="legacy（修正前,绝对 5㎡ + 三元组全等 + n_S=1 INVALID）",
    )
    corrected = compute_coverage(
        targets, references, config=CORRECTED, config_name="corrected（第 2 期四条修正）",
    )
    print(render_coverage(legacy))
    print()
    print(render_coverage(corrected))
    print()
    print(_decision(corrected))
    return 0


def _decision(report) -> str:
    s = report.s_grade_coverage
    if s >= 0.60:
        verdict = (
            "s_grade_coverage ≥ 60%:按 2.2 六级原样定稿,S 级主导。"
            "「六级压成三级」不执行。"
        )
    elif s >= 0.20:
        verdict = (
            "s_grade_coverage 落在 20–60%。第 2 期已否决压成三级;"
            "若修正后仍在此档,保持六级,力气放在回退链与 confidence。"
        )
    else:
        verdict = (
            "s_grade_coverage < 20%:S 级不再作为主路径独立基准层,"
            "主路径改回「同室数 + 面积分层」。"
        )
    return "定稿建议:" + verdict


if __name__ == "__main__":
    raise SystemExit(main())
