#!/usr/bin/env python3
"""Build Phase 2B target manifests strictly from workspace Phase 1 & 2A outputs.

Outputs:
1. phase2b_p1p2_targets.json (89 sites: 47 P1 + 42 P2)
2. phase2b1_deep_targets.json (33 sites: 23 confirmed-28 intranet + 6 2A handoff + 4 2A denied_403 recheck)
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PHASE1_DIFF_PATH = WORKSPACE_ROOT / "phase1_diff.md"
PHASE1_DISCOVERY_PATH = WORKSPACE_ROOT / "phase1_discovery.json"
HANDOFF_2A_PATH = WORKSPACE_ROOT / "phase2a_handoff_to_2b.json"
PROFILES_2A_PATH = WORKSPACE_ROOT / "site_profiles_2a.jsonl"

OUT_P1P2_PATH = WORKSPACE_ROOT / "phase2b_p1p2_targets.json"
OUT_DEEP_2B1_PATH = WORKSPACE_ROOT / "phase2b1_deep_targets.json"

HOST_RE = re.compile(
    r"`([a-z0-9.-]+\.(?:ke\.com|lianjia\.com|homelink\.com\.cn|deyoulife\.com))`",
    re.I,
)

CONFIRMED_28_FALLBACK_META: dict[str, dict[str, str]] = {
    "shouye.link.lianjia.com": {"name": "工作台首页", "url": "https://shouye.link.lianjia.com/"},
    "house.link.lianjia.com": {"name": "房源系统", "url": "https://house.link.lianjia.com/"},
    "lease-pz.link.lianjia.com": {"name": "普租租赁系统", "url": "https://lease-pz.link.lianjia.com/"},
    "trusteeship.link.lianjia.com": {"name": "托管业务系统", "url": "https://trusteeship.link.lianjia.com/"},
    "link.fang.lianjia.com": {"name": "新房 / A+ 统一顶栏", "url": "https://link.fang.lianjia.com/map"},
    "ke.deyoulife.com": {"name": "德佑客源系统", "url": "https://ke.deyoulife.com/"},
    "ke.link.lianjia.com": {"name": "客源系统", "url": "https://ke.link.lianjia.com/"},
    "hetong.link.lianjia.com": {"name": "签约合同系统", "url": "https://hetong.link.lianjia.com/"},
    "xiaoxi.link.lianjia.com": {"name": "消息系统", "url": "https://xiaoxi.link.lianjia.com/"},
    "odin.ke.com": {"name": "Odin 数据平台 / 管理驾驶舱", "url": "https://odin.ke.com/"},
    "kemcc.ke.com": {"name": "通知中心（kemcc）", "url": "https://kemcc.ke.com/"},
    "family.ke.com": {"name": "员工门户", "url": "https://family.ke.com/home/portal"},
    "hr.ke.com": {"name": "HR 门户（KeHR）", "url": "https://hr.ke.com/"},
    "saas.a.ke.com": {"name": "A+ 客户端认证服务", "url": "https://saas.a.ke.com/"},
    "study.ke.com": {"name": "贝壳学堂 / 在线学习", "url": "https://study.ke.com/"},
    "xinchou.ke.com": {"name": "薪酬核算系统", "url": "https://xinchou.ke.com/"},
}

BAD_PATH_MARKERS = (
    "/error/",
    "/unauthorized",
    "/forbidden",
    "/no-permission",
    "/nopermission",
    "/401",
    "/403",
    "/owa/auth/logon",
)


def parse_diff_md_names(diff_md: str) -> dict[str, str]:
    """Extract inline Chinese system names `host`（名称） from phase1_diff.md."""
    names: dict[str, str] = {}
    for m in re.finditer(
        r"`([a-z0-9.-]+\.(?:ke\.com|lianjia\.com|homelink\.com\.cn|deyoulife\.com))`[（(]([^）)\n]{1,40})[）)]",
        diff_md,
        flags=re.I,
    ):
        host = m.group(1).lower()
        label = m.group(2).strip()
        if label and host not in names:
            names[host] = label
    return names


def parse_section_3_2_lists(diff_md: str) -> dict[str, list[str]]:
    """Parse confirmed-28 intranet, P0, P1, and P2 host lists from Section 3.2."""
    m = re.search(r"### 3\.2 Batch 2B：.*", diff_md, flags=re.S)
    if not m:
        raise ValueError("Could not locate Section 3.2 in phase1_diff.md")
    sec = m.group(0)

    result: dict[str, list[str]] = {
        "confirmed_28_intranet": [],
        "p0": [],
        "p1": [],
        "p2": [],
    }
    for line in sec.splitlines():
        s = line.strip()
        if not s.startswith("- "):
            continue
        if "已确认 28 表中需内网可达的其余站点" in s:
            key = "confirmed_28_intranet"
        elif "P0 核心业务系统" in s:
            key = "p0"
        elif "P1 员工高频协作" in s:
            key = "p1"
        elif "P2 产研基建与 HR/管理后台" in s:
            key = "p2"
        else:
            continue
        seen = set(result[key])
        for h in HOST_RE.findall(s):
            hl = h.lower()
            if hl not in seen:
                seen.add(hl)
                result[key].append(hl)
    return result


def index_discovery(discovery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index clean URLs and labels per host from phase1_discovery.json."""
    info: dict[str, dict[str, Any]] = {}

    def ensure(host: str) -> dict[str, Any]:
        if host not in info:
            info[host] = {"labels": [], "urls": [], "hub_entry_url": "", "hub_name": ""}
        return info[host]

    for hub in discovery.get("hubs", []):
        entry_url = (hub.get("entry_url") or "").strip()
        final_url = (hub.get("final_url") or "").strip()
        hub_name = (hub.get("hub_name") or "").strip()
        for u in (final_url, entry_url):
            if u and "[REDACTED]" not in u:
                h = (urllib.parse.urlsplit(u).hostname or "").lower()
                if h:
                    rec = ensure(h)
                    if not rec["hub_entry_url"]:
                        rec["hub_entry_url"] = u
                    if hub_name and not rec["hub_name"]:
                        rec["hub_name"] = hub_name
                    if u not in rec["urls"]:
                        rec["urls"].append(u)

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

    for items in discovery.get("diff_analysis", {}).get("categories", {}).values():
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


def pick_best_url(host: str, rec: dict[str, Any], allow_override: bool = True) -> str:
    if allow_override and host in CONFIRMED_28_FALLBACK_META:
        return CONFIRMED_28_FALLBACK_META[host]["url"]

    urls: list[str] = list(rec.get("urls") or [])
    if not urls:
        return f"https://{host}/"

    filtered = [
        u for u in urls if not any(bad in u.lower() for bad in BAD_PATH_MARKERS)
    ]
    pool = filtered if filtered else urls

    def score(u: str) -> tuple[int, int, int, int, str]:
        p = urllib.parse.urlsplit(u)
        is_http_only = 0 if p.scheme == "https" else 1
        is_root = 0 if (p.path in ("", "/") and not p.query and not p.fragment) else 1
        has_query = 1 if p.query else 0
        return (is_http_only, is_root, has_query, len(u), u)

    return sorted(pool, key=score)[0]


def pick_best_name(
    host: str,
    rec: dict[str, Any],
    diff_names: dict[str, str],
) -> str:
    if host in CONFIRMED_28_FALLBACK_META:
        return CONFIRMED_28_FALLBACK_META[host]["name"]
    if host in diff_names:
        return diff_names[host]
    if rec.get("hub_name"):
        return str(rec["hub_name"])
    labels = rec.get("labels") or []
    if labels:
        return " / ".join(labels[:2])
    return host


def infer_auth_hint(host: str) -> str:
    hl = host.lower()
    if "wandou" in hl:
        return "wandou_localstorage_token"
    if "moma" in hl:
        return "moma_supabase_cas"
    return "cas_cookie"


def main() -> None:
    diff_md = PHASE1_DIFF_PATH.read_text(encoding="utf-8")
    discovery = json.loads(PHASE1_DISCOVERY_PATH.read_text(encoding="utf-8"))
    handoff_data = json.loads(HANDOFF_2A_PATH.read_text(encoding="utf-8"))

    diff_names = parse_diff_md_names(diff_md)
    sec_lists = parse_section_3_2_lists(diff_md)
    disc_index = index_discovery(discovery)

    # 1. Build 89 P1/P2 screening targets
    p1p2_targets: list[dict[str, Any]] = []
    for priority, key, cat in (
        ("P1", "p1", "p1_collab_finance_hr"),
        ("P2", "p2", "p2_rnd_infra_hr_admin"),
    ):
        for host in sec_lists[key]:
            rec = disc_index.get(host, {})
            entry_url = pick_best_url(host, rec, allow_override=False)
            name = pick_best_name(host, rec, diff_names)
            p1p2_targets.append(
                {
                    "host": host,
                    "name": name,
                    "url": entry_url,
                    "entry_url": entry_url,
                    "priority": priority,
                    "category": cat,
                    "batch": "2B-2",
                    "auth_hint": infer_auth_hint(host),
                }
            )

    p1_cnt = sum(1 for t in p1p2_targets if t["priority"] == "P1")
    p2_cnt = sum(1 for t in p1p2_targets if t["priority"] == "P2")
    if p1_cnt != 47 or p2_cnt != 42 or len(p1p2_targets) != 89:
        raise RuntimeError(
            f"Unexpected P1/P2 counts: P1={p1_cnt}, P2={p2_cnt}, total={len(p1p2_targets)}"
        )

    # 2. Build 33 Phase 2B-1 deep profiling targets
    deep_targets: list[dict[str, Any]] = []
    seen_deep: set[str] = set()

    # 2a. 23 confirmed-28 intranet baseline hosts
    for host in sec_lists["confirmed_28_intranet"]:
        rec = disc_index.get(host, {})
        entry_url = pick_best_url(host, rec, allow_override=True)
        name = pick_best_name(host, rec, diff_names)
        deep_targets.append(
            {
                "host": host,
                "name": name,
                "url": entry_url,
                "entry_url": entry_url,
                "category": "confirmed_28_intranet",
                "batch": "2B-1",
                "auth_hint": infer_auth_hint(host),
            }
        )
        seen_deep.add(host)

    # 2b. 6 handoff targets from Phase 2A
    handoff_list = handoff_data.get("targets", [])
    for item in handoff_list:
        host = (item.get("host") or "").lower()
        if not host or host in seen_deep:
            continue
        url = item.get("url") or f"https://{host}/"
        deep_targets.append(
            {
                "host": host,
                "name": item.get("name") or host,
                "url": url,
                "entry_url": url,
                "category": "handoff_from_2a",
                "source_category": item.get("category", ""),
                "batch": "2B-1",
                "auth_hint": item.get("auth_hint") or infer_auth_hint(host),
            }
        )
        seen_deep.add(host)

    # 2c. 4 denied_403 recheck targets from site_profiles_2a.jsonl
    denied_403_count = 0
    for line in PROFILES_2A_PATH.read_text(encoding="utf-8").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        rec = json.loads(line_s)
        if rec.get("access_verdict") != "denied_403":
            continue
        host = (rec.get("host") or "").lower()
        if not host or host in seen_deep:
            continue
        url = rec.get("entry_url") or f"https://{host}/"
        deep_targets.append(
            {
                "host": host,
                "name": rec.get("name") or host,
                "url": url,
                "entry_url": url,
                "category": "denied_403_recheck",
                "source_category": rec.get("category", ""),
                "batch": "2B-1",
                "auth_hint": infer_auth_hint(host),
            }
        )
        seen_deep.add(host)
        denied_403_count += 1

    if len(deep_targets) != 33:
        raise RuntimeError(f"Expected 33 deep 2B-1 targets, got {len(deep_targets)}")

    OUT_P1P2_PATH.write_text(
        json.dumps(p1p2_targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    OUT_DEEP_2B1_PATH.write_text(
        json.dumps(deep_targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "p1p2_total": len(p1p2_targets),
                "p1": p1_cnt,
                "p2": p2_cnt,
                "deep_2b1_total": len(deep_targets),
                "confirmed_28_intranet": len(sec_lists["confirmed_28_intranet"]),
                "handoff_from_2a": len(handoff_list),
                "denied_403_recheck": denied_403_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
