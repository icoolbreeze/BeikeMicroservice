#!/usr/bin/env python3
"""Generate phase2b_p1p2_deep_targets.json (72 P1/P2 sites) from screening outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SCREEN_JSON_PATH = WORKSPACE_ROOT / "phase2b_p1p2_screen.json"
TARGETS_JSON_PATH = WORKSPACE_ROOT / "phase2b_p1p2_targets.json"
PROFILES_2B_PATH = WORKSPACE_ROOT / "site_profiles_2b.jsonl"
OUT_DEEP_PATH = WORKSPACE_ROOT / "phase2b_p1p2_deep_targets.json"

ENTER_2B2_VERDICTS = {"accessible_verified_or_page", "spa_shell_needs_browser"}


def main() -> None:
    screen_data = json.loads(SCREEN_JSON_PATH.read_text(encoding="utf-8"))
    all_targets = json.loads(TARGETS_JSON_PATH.read_text(encoding="utf-8"))

    target_by_host: dict[str, dict[str, Any]] = {}
    for item in all_targets:
        host = (item.get("host") or "").strip().lower()
        if not host:
            continue
        if host in target_by_host:
            raise RuntimeError(f"Duplicate host in {TARGETS_JSON_PATH.name}: {host}")
        target_by_host[host] = item

    screen_by_host: dict[str, dict[str, Any]] = {}
    for rec in screen_data.get("results", []):
        host = (rec.get("host") or "").strip().lower()
        if host:
            screen_by_host[host] = rec

    enter_hosts_raw = [h.strip().lower() for h in screen_data.get("enter_2b2_hosts", []) if h.strip()]
    verdict_hosts = [
        h
        for h, rec in screen_by_host.items()
        if rec.get("verdict") in ENTER_2B2_VERDICTS
    ]
    if set(enter_hosts_raw) != set(verdict_hosts):
        raise RuntimeError(
            "Mismatch between enter_2b2_hosts and screened verdicts in phase2b_p1p2_screen.json"
        )
    if len(enter_hosts_raw) != 72 or len(set(enter_hosts_raw)) != 72:
        raise RuntimeError(
            f"Expected 72 unique enter_2b2_hosts, got {len(enter_hosts_raw)}"
        )

    existing_2b_hosts: set[str] = set()
    if PROFILES_2B_PATH.exists():
        for line in PROFILES_2B_PATH.read_text(encoding="utf-8").splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            rec = json.loads(line_s)
            h = (rec.get("host") or "").strip().lower()
            if h:
                existing_2b_hosts.add(h)

    overlap = sorted(set(enter_hosts_raw) & existing_2b_hosts)
    if overlap:
        raise RuntimeError(
            f"Unexpected overlap with {PROFILES_2B_PATH.name}: {overlap}"
        )

    deep_targets: list[dict[str, Any]] = []
    for host in enter_hosts_raw:
        if host not in target_by_host:
            raise RuntimeError(f"Host missing from {TARGETS_JSON_PATH.name}: {host}")
        src = target_by_host[host]
        scr = screen_by_host[host]
        entry_url = src.get("entry_url") or src.get("url") or scr.get("entry_url") or f"https://{host}/"
        deep_targets.append(
            {
                "host": host,
                "name": src.get("name") or scr.get("name") or host,
                "url": entry_url,
                "entry_url": entry_url,
                "priority": src.get("priority") or scr.get("priority") or "",
                "category": src.get("category") or "",
                "batch": "2B-P1P2",
                "auth_hint": src.get("auth_hint") or "cas_cookie",
                "screen_verdict": scr.get("verdict", ""),
            }
        )

    p1_count = sum(1 for item in deep_targets if item["priority"] == "P1")
    p2_count = sum(1 for item in deep_targets if item["priority"] == "P2")
    if p1_count != 41 or p2_count != 31 or len(deep_targets) != 72:
        raise RuntimeError(
            f"Unexpected deep target counts: total={len(deep_targets)}, P1={p1_count}, P2={p2_count}"
        )

    OUT_DEEP_PATH.write_text(
        json.dumps(deep_targets, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "total": len(deep_targets),
                "p1": p1_count,
                "p2": p2_count,
                "overlap_with_2b": len(overlap),
                "output": OUT_DEEP_PATH.name,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
