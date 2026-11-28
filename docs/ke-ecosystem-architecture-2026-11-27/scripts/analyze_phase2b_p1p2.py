#!/usr/bin/env python3
"""Inspect Phase 2B-P1P2 profiling results for summary generation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PROFILES_PATH = WORKSPACE_ROOT / "site_profiles_2b_p1p2.jsonl"
SCREEN_PATH = WORKSPACE_ROOT / "phase2b_p1p2_screen.json"
TARGETS_PATH = WORKSPACE_ROOT / "phase2b_p1p2_deep_targets.json"


def print_safe_kw_and_apis(recs: list[dict[str, Any]]) -> None:
    known_ui: set[str] = set()
    for fn in ("site_profiles_2a.jsonl", "site_profiles_2b.jsonl"):
        p = WORKSPACE_ROOT / fn
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("title"):
                known_ui.add(r["title"])
            for vals in r.get("ui_modules", {}).values():
                if isinstance(vals, list):
                    known_ui.update(str(v) for v in vals)

    disc_p = WORKSPACE_ROOT / "phase1_discovery.json"
    if disc_p.exists():
        disc = json.loads(disc_p.read_text(encoding="utf-8"))
        for h in disc.get("hubs", []):
            for l in h.get("extracted_links", []):
                if l.get("label"):
                    known_ui.add(l["label"])

    print(f"Known clean UI string count: {len(known_ui)}")
    cjk_freq: dict[str, int] = {}
    for r in recs:
        h = r["host"]
        ui = r.get("ui_modules", {})
        for f, vals in [
            ("title", [r.get("title", "")]),
            ("top_nav", ui.get("top_nav", [])),
            ("side", ui.get("side_nav_or_tabs", [])),
            ("cards", ui.get("page_headings_or_cards", [])),
        ]:
            for idx, v in enumerate(vals):
                s = str(v or "")
                if not s or s in known_ui:
                    continue
                m = re.search(r"(你好|欢迎|工号|手机|邮箱|姓名|\[REDACTED)", s)
                if m:
                    is_cjk = bool(re.fullmatch(r"[\u4e00-\u9fff]+", s))
                    print(f"KW_HIT {h} {f}[{idx}] len={len(s)} kw={m.group(1)!r} all_cjk={is_cjk}")
                if re.search(r"\d{6,}|@", s):
                    print(f"DIGITS_AT_HIT {h} {f}[{idx}] len={len(s)}")
                if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", s):
                    cjk_freq[s] = cjk_freq.get(s, 0) + 1
        # Check backend_apis paths for PII characters
        for api in r.get("backend_apis", []):
            p = api.get("path", "")
            if re.search(r"[\u4e00-\u9fff]|1[3-9]\d{9}|@", p):
                print(f"API_NONASCII {h} {api['method']} {api['host']} len={len(p)}")
    print("Top repeated unknown cjk_2_4 count:", sum(1 for k, v in cjk_freq.items() if v > 1), "singletons:", sum(1 for k, v in cjk_freq.items() if v == 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-ui", action="store_true", help="Print titles and UI modules (only after PII scrubbing).")
    parser.add_argument("--safe-deep", action="store_true", help="Print APIs, cookies, final_url, and anonymized PII checks.")
    args = parser.parse_args()

    recs = [
        json.loads(line)
        for line in PROFILES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    screen = json.loads(SCREEN_PATH.read_text(encoding="utf-8"))
    targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    t_map = {t["host"]: t for t in targets}
    s_map = {r["host"]: r for r in screen["results"]}

    if args.safe_deep:
        print_safe_kw_and_apis(recs)
        print("-" * 60)
        for i, r in enumerate(recs, 1):
            h = r["host"]
            t = t_map[h]
            s = s_map[h]
            apis = [f"{a['method']} {a['host']}{a['path']}({a['status']}/{a['biz_code']})" for a in r.get("backend_apis", [])]
            print(
                f"{i:2d} | {t['priority']} | {h} | {t['name']} | scr={s['verdict']} -> prof={r['access_verdict']} | "
                f"http={r['http_status']} | id={r['identity_match']} | sso={r['auth_mechanism']['sso_type']} | "
                f"final_url={r['final_url']} | cookies={r['auth_mechanism']['issued_cookie_names']} | apis={apis}"
            )
        return

    print(f"Total records: {len(recs)}, unique hosts: {len({r['host'] for r in recs})}")
    for i, r in enumerate(recs, 1):
        h = r["host"]
        t = t_map[h]
        s = s_map[h]
        ui = r.get("ui_modules", {})
        suspicious = []
        for f, vals in [
            ("title", [r.get("title", "")]),
            ("top_nav", ui.get("top_nav", [])),
            ("side", ui.get("side_nav_or_tabs", [])),
            ("cards", ui.get("page_headings_or_cards", [])),
        ]:
            for v in vals:
                if not v:
                    continue
                if re.search(r"\d{6,}|[@:]|你好|欢迎|工号|手机|邮箱|姓名|\[REDACTED", str(v)):
                    suspicious.append(f"{f}:len={len(str(v))}")
        apis = [f"{a['method']} {a['host']}{a['path']}({a['status']}/{a['biz_code']})" for a in r.get("backend_apis", [])]
        print(
            f"{i:2d} | {t['priority']} | {h} | {t['name']} | scr={s['verdict']} | prof={r['access_verdict']} | "
            f"http={r['http_status']} | id={r['identity_match']} | sso={r['auth_mechanism']['sso_type']} | "
            f"elapsed={r['elapsed_seconds']}s | redirect={r['redirect_hosts']} | "
            f"api_hosts={r['backend_api_hosts']} | apis_cnt={len(apis)} | susp={suspicious} | err={r['errors']}"
        )
        if args.show_ui:
            print(f"    final_url: {r['final_url']}")
            print(f"    title: {r['title']!r}")
            print(f"    cookies: {r['auth_mechanism']['issued_cookie_names']}")
            print(f"    top_nav: {ui.get('top_nav', [])}")
            print(f"    side_nav: {ui.get('side_nav_or_tabs', [])}")
            print(f"    cards: {ui.get('page_headings_or_cards', [])}")
            print(f"    shadow_dom: {ui.get('shadow_dom_detected')}")
            print(f"    apis: {apis[:15]}")


if __name__ == "__main__":
    main()
