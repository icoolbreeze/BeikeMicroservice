#!/usr/bin/env python3
"""Generate phase2a_targets.json (22 sites) from phase1_diff.md Section 3.1 and phase1_discovery.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PHASE1_DIFF_PATH = WORKSPACE_ROOT / "phase1_diff.md"
PHASE1_DISCOVERY_PATH = WORKSPACE_ROOT / "phase1_discovery.json"
OUTPUT_TARGETS_PATH = WORKSPACE_ROOT / "phase2a_targets.json"

BASELINE_METADATA: dict[str, dict[str, str]] = {
    "helper.ke.com": {
        "name": "客服中心 / 业绩争议",
        "url": "https://helper.ke.com/",
        "auth_hint": "cas_cookie",
    },
    "letsai.ke.com": {
        "name": "AI 门户",
        "url": "https://letsai.ke.com/",
        "auth_hint": "cas_cookie",
    },
    "moma.ke.com": {
        "name": "Moma 轻应用平台",
        "url": "https://moma.ke.com/",
        "auth_hint": "moma_supabase_cas",
    },
    "wandou.ke.com": {
        "name": "豌豆（CRM 普租 IM）",
        "url": "https://wandou.ke.com/",
        "auth_hint": "wandou_localstorage_token",
    },
    "listing-de.ke.com": {
        "name": "商铺写字楼",
        "url": "https://listing-de.ke.com/",
        "auth_hint": "cas_cookie",
    },
}

FALLBACK_HOST_META: dict[str, dict[str, str]] = {
    "app-moma.ke.com": {
        "name": "Moma 轻应用（page_only_unverified）",
        "url": "https://app-moma.ke.com/",
        "auth_hint": "moma_supabase_cas",
    },
}


def parse_section_3_1_hosts(diff_md: str) -> list[tuple[str, str]]:
    """Parse Batch 2A hosts and subgroup categories from phase1_diff.md Section 3.1."""
    m = re.search(
        r"### 3\.1 Batch 2A：.*?(?=### 3\.2 Batch 2B：|\Z)",
        diff_md,
        flags=re.S,
    )
    if not m:
        raise ValueError("Could not locate Section 3.1 in phase1_diff.md")
    sec = m.group(0)

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in sec.splitlines():
        line_s = line.strip()
        if not line_s.startswith("- "):
            continue
        if "已验证直连基线" in line_s:
            cat = "baseline_public_direct"
        elif "AI 门户内网体系新站点" in line_s:
            cat = "ai_portal_candidate"
        elif "客服中心关联协同系统" in line_s:
            cat = "help_center_related"
        elif "page_only_unverified" in line_s:
            cat = "reappeared_page_only_unverified"
        else:
            continue

        for host in re.findall(r"`([a-z0-9.-]+\.(?:ke\.com|lianjia\.com))`", line_s, flags=re.I):
            hl = host.lower()
            if hl not in seen:
                seen.add(hl)
                ordered.append((hl, cat))
    return ordered


def index_discovery_hosts(discovery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect best labels and clean sample URLs per host from phase1_discovery.json."""
    info: dict[str, dict[str, Any]] = {}

    def ensure(host: str) -> dict[str, Any]:
        if host not in info:
            info[host] = {"labels": [], "urls": []}
        return info[host]

    for hub in discovery.get("hubs", []):
        for link in hub.get("extracted_links", []):
            h = (link.get("host") or "").lower()
            if not h:
                continue
            rec = ensure(h)
            lbl = (link.get("label") or "").strip()
            url = (link.get("sanitized_url") or "").strip()
            if lbl and lbl != h and lbl not in rec["labels"]:
                rec["labels"].append(lbl)
            if url and "[REDACTED]" not in url and url not in rec["urls"]:
                rec["urls"].append(url)

    cats = discovery.get("diff_analysis", {}).get("categories", {})
    for items in cats.values():
        for item in items:
            h = (item.get("host") or "").lower()
            if not h:
                continue
            rec = ensure(h)
            for lbl in item.get("labels", []):
                if lbl and lbl != h and lbl not in rec["labels"]:
                    rec["labels"].append(lbl)
            for url in item.get("sample_urls", []):
                if url and "[REDACTED]" not in url and url not in rec["urls"]:
                    rec["urls"].append(url)
    return info


def pick_best_url_and_name(
    host: str, category: str, disc_info: dict[str, dict[str, Any]]
) -> tuple[str, str, str]:
    if host in BASELINE_METADATA:
        m = BASELINE_METADATA[host]
        return m["url"], m["name"], m["auth_hint"]
    if host in FALLBACK_HOST_META:
        m = FALLBACK_HOST_META[host]
        return m["url"], m["name"], m["auth_hint"]

    rec = disc_info.get(host, {"labels": [], "urls": []})
    labels: list[str] = rec["labels"]
    urls: list[str] = rec["urls"]

    # Prefer concise, clean entry URLs
    chosen_url = f"https://{host}/"
    if host == "chatbi.ke.com":
        chosen_url = "https://chatbi.ke.com/"
    elif host == "luoshu.ke.com":
        # Prefer clean help_center dynamicFormRender or prodApp URL without [REDACTED]
        clean = [u for u in urls if "dynamicFormRender" in u] or [
            u for u in urls if "prodApp" in u
        ]
        chosen_url = clean[0] if clean else f"https://{host}/"
    elif host == "jixiao.ke.com":
        clean = [u for u in urls if u.startswith("https://") and "/p/home" in u] or [
            u for u in urls if u.startswith("https://")
        ]
        chosen_url = clean[0] if clean else f"https://{host}/"
    elif host == "app-fly.ke.com":
        clean = [u for u in urls if "employee-relations-hub" in u] or urls
        chosen_url = clean[0] if clean else f"https://{host}/"
    elif host == "is-prod.ke.com":
        clean = [u for u in urls if "/pages/chat" in u] or urls
        chosen_url = clean[0] if clean else f"https://{host}/"
    elif host == "bella.ke.com":
        chosen_url = "https://bella.ke.com/"
    elif urls:
        https_urls = [u for u in urls if u.startswith("https://")]
        chosen_url = https_urls[0] if https_urls else urls[0]

    # Build concise Chinese name
    if host == "bella.ke.com":
        name = "Bella（招聘与面试 AI 助手）"
    elif host == "is-prod.ke.com":
        name = "闹海 / 小伯乐"
    elif host == "luoshu.ke.com":
        name = "洛书动态表单（首看保护/印章/访客/干部）"
    elif host == "app-fly.ke.com":
        name = "员工文化应用（员工关系/新文化内核/品牌资源）"
    elif host == "jixiao.ke.com":
        name = "绩效与试用期系统"
    elif labels:
        name = " / ".join(labels[:3])
    else:
        name = host

    auth_hint = "moma_supabase_cas" if "moma" in host else "cas_cookie"
    return chosen_url, name, auth_hint


def main() -> None:
    diff_md = PHASE1_DIFF_PATH.read_text(encoding="utf-8")
    discovery = json.loads(PHASE1_DISCOVERY_PATH.read_text(encoding="utf-8"))
    disc_info = index_discovery_hosts(discovery)

    parsed_hosts = parse_section_3_1_hosts(diff_md)
    targets: list[dict[str, Any]] = []
    for host, category in parsed_hosts:
        url, name, auth_hint = pick_best_url_and_name(host, category, disc_info)
        targets.append(
            {
                "host": host,
                "name": name,
                "url": url,
                "category": category,
                "batch": "2A",
                "auth_hint": auth_hint,
            }
        )

    if len(targets) != 22:
        raise RuntimeError(f"Expected 22 Batch 2A targets, got {len(targets)}")

    OUTPUT_TARGETS_PATH.write_text(
        json.dumps(targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(targets)} targets to {OUTPUT_TARGETS_PATH.name}")


if __name__ == "__main__":
    main()
