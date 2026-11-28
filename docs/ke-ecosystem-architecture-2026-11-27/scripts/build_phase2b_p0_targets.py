#!/usr/bin/env python3
"""Generate phase2b_p0_targets.json (39 P0 core business targets) from Phase 1 artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_phase2b_targets as base  # noqa: E402

OUT_P0_PATH = WORKSPACE_ROOT / "phase2b_p0_targets.json"
DEEP_2B1_PATH = WORKSPACE_ROOT / "phase2b1_deep_targets.json"
COMBINED_TMP_PATH = WORKSPACE_ROOT / "_tmp_2b1_p0_combined_targets.json"


def pick_p0_url(host: str, rec: dict[str, Any]) -> str:
    urls: list[str] = list(rec.get("urls") or [])
    if not urls:
        return f"https://{host}/"
    filtered = [
        u for u in urls if not any(bad in u.lower() for bad in base.BAD_PATH_MARKERS)
    ]
    if not filtered:
        # Avoid directly hitting portal-side /forbidden or /no-permission links; test root entry.
        return f"https://{host}/"
    return base.pick_best_url(host, {"urls": filtered}, allow_override=False)


def clean_p0_name(host: str, rec: dict[str, Any], diff_names: dict[str, str]) -> str:
    if host == "oomp.ke.com":
        return "二手自然商机展位"
    name = base.pick_best_name(host, rec, diff_names)
    return name.split("，与已有")[0].strip()


def main() -> None:
    diff_md = base.PHASE1_DIFF_PATH.read_text(encoding="utf-8")
    discovery = json.loads(base.PHASE1_DISCOVERY_PATH.read_text(encoding="utf-8"))

    diff_names = base.parse_diff_md_names(diff_md)
    sec_lists = base.parse_section_3_2_lists(diff_md)
    disc_index = base.index_discovery(discovery)

    p0_hosts = sec_lists["p0"]
    if len(p0_hosts) != 39 or len(set(p0_hosts)) != 39:
        raise RuntimeError(f"Expected 39 unique P0 hosts, got {len(p0_hosts)}")

    p0_targets: list[dict[str, Any]] = []
    for host in p0_hosts:
        rec = disc_index.get(host, {})
        entry_url = pick_p0_url(host, rec)
        name = clean_p0_name(host, rec, diff_names)
        p0_targets.append(
            {
                "host": host,
                "name": name,
                "url": entry_url,
                "entry_url": entry_url,
                "priority": "P0",
                "category": "p0_core_business",
                "batch": "2B-P0",
                "auth_hint": base.infer_auth_hint(host),
            }
        )

    OUT_P0_PATH.write_text(
        json.dumps(p0_targets, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    deep_2b1 = json.loads(DEEP_2B1_PATH.read_text(encoding="utf-8"))
    combined = deep_2b1 + p0_targets
    combined_hosts = [item["host"].lower() for item in combined]
    if len(combined) != 72 or len(set(combined_hosts)) != 72:
        raise RuntimeError(
            f"Combined target list integrity check failed: total={len(combined)}, unique={len(set(combined_hosts))}"
        )

    COMBINED_TMP_PATH.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "p0_count": len(p0_targets),
                "combined_count": len(combined),
                "output": OUT_P0_PATH.name,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
