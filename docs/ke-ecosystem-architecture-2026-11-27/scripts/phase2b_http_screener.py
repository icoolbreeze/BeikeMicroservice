#!/usr/bin/env python3
"""Lightweight read-only HTTP/SSO screener for Phase 2B-1 (89 P1/P2 sites).

Features:
- Managed temporary SSH SOCKS5 tunnel via try...finally (never touches existing tunnels)
- Memory-only session material loading via short-lived SSH read
- Single baseline intranet liveness probe before concurrent screening
- Concurrent GET-only screening (max_workers=4, 10s per-site timeout)
- Isolated per-site cookie jar; blocks /logout|/signout|/logoff and external-domain redirects
- Strictly redacted outputs: phase2b_p1p2_screen.json & phase2b_p1p2_screen.md
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html as html_lib
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase2_site_profiler import (  # noqa: E402
    CORP_ROOT_DOMAINS,
    DEFAULT_REMOTE_CRED_PATH,
    DEFAULT_SSH_HOST,
    LOGOUT_PATH_RE,
    SSO_LOGIN_HOSTS,
    ManagedSocksTunnel,
    load_credentials_in_memory,
    parent_domains_for_host,
    recursive_scrub,
    sanitize_text,
    sanitize_url,
)

WORKSPACE_ROOT = SCRIPT_DIR.parent
DEFAULT_TARGETS_JSON = WORKSPACE_ROOT / "phase2b_p1p2_targets.json"
DEFAULT_OUTPUT_JSON = WORKSPACE_ROOT / "phase2b_p1p2_screen.json"
DEFAULT_OUTPUT_MD = WORKSPACE_ROOT / "phase2b_p1p2_screen.md"

BASELINE_PROBE_TARGET = {
    "host": "family.ke.com",
    "name": "员工门户基线探活",
    "priority": "BASELINE",
    "url": "https://family.ke.com/home/portal",
}

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECT_HOPS = 12

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
PASSWORD_INPUT_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*['\"]?password['\"]?", re.I)
SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style|noscript)[^>]*>.*?</(?:script|style|noscript)>",
    re.I | re.S,
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TAG_RE = re.compile(r"<[^>]+>")

SPA_MARKERS_RE = re.compile(
    r"(?:<div\s+id=['\"](?:app|root|__next|main)['\"]|window\.__INITIAL_STATE__|"
    r"<script[^>]+type=['\"]module['\"]|chunk-vendors|webpackJsonp|"
    r"Please enable JavaScript|需要启用\s*JavaScript)",
    re.I,
)


def is_corp_domain(host: str) -> bool:
    hl = (host or "").strip().lower().rstrip(".")
    if not hl:
        return False
    return any(hl == d or hl.endswith(f".{d}") for d in CORP_ROOT_DOMAINS)


def build_isolated_cookies(
    business: dict[str, Any], refresh: dict[str, Any], target_host: str
) -> httpx.Cookies:
    jar = httpx.Cookies()
    for domain in parent_domains_for_host(target_host):
        for name, val in business.items():
            if isinstance(val, str) and val:
                jar.set(name, val, domain=domain, path="/")
    for name in ("TGC", "TGC_Secure"):
        val = refresh.get(name)
        if isinstance(val, str) and val:
            jar.set(name, val, domain="login.ke.com", path="/")
    return jar


def sanitize_url_strict(
    raw_url: str, sensitive_values: set[str], base_url: str = ""
) -> tuple[str, str]:
    san, host = sanitize_url(raw_url, sensitive_values, base_url=base_url)
    if not san:
        return "", ""
    san = re.sub(
        r"(?i)(;(?:jsessionid|sessionid|sid|token)=)[^;/?#]+",
        r"\1[REDACTED]",
        san,
    )
    return san, host


def extract_title_and_visible_text(raw_html: str) -> tuple[str, str]:
    title_match = TITLE_RE.search(raw_html)
    title = html_lib.unescape(title_match.group(1)).strip() if title_match else ""
    stripped = COMMENT_RE.sub(" ", raw_html)
    stripped = SCRIPT_STYLE_RE.sub(" ", stripped)
    stripped = TAG_RE.sub(" ", stripped)
    visible = re.sub(r"\s+", " ", html_lib.unescape(stripped)).strip()
    return title, visible


def classify_http_response(
    http_status: int | None,
    final_url: str,
    final_host: str,
    raw_html: str,
    title: str,
    visible_text: str,
    cas_callback_seen: bool,
    identity_match: bool,
    issued_cookie_names: list[str],
    redirect_loop: bool,
    blocked_external: bool,
    blocked_external_host: str,
    error_msg: str | None,
) -> str:
    if blocked_external:
        if any(
            kw in blocked_external_host
            for kw in ("weixin", "dingtalk", "feishu", "oauth", "sso", "login", "auth", "dooioo")
        ):
            return "login_required"
        return "unreachable_or_error"

    if error_msg and http_status is None:
        return "unreachable_or_error"

    if http_status is not None and (http_status >= 500 or http_status == 404):
        return "unreachable_or_error"

    if redirect_loop:
        return "login_required" if cas_callback_seen else "unreachable_or_error"

    # Denied / 403 detection
    lower_u = final_url.lower()
    if http_status in (401, 403):
        return "denied_403"
    if final_host in {"hip.ke.com", "hip.lianjia.com"}:
        return "denied_403"
    if any(
        seg in lower_u
        for seg in (
            "/forbidden",
            "/no-permission",
            "/nopermission",
            "/no-access",
            "/noaccess",
            "/noauth",
            "/401",
            "/403",
            "/unauthorized",
        )
    ):
        return "denied_403"
    if re.search(r"\b403\b|forbidden|无权限|暂无权限|没有访问权限|未授权访问", title, re.I):
        return "denied_403"
    if len(visible_text) < 500 and re.search(
        r"无权访问|暂无访问权限|您没有权限|403\s+Forbidden|Access\s+Denied",
        visible_text,
        re.I,
    ):
        return "denied_403"

    # Login page detection
    parsed_final = urllib.parse.urlsplit(final_url)
    path_lower = parsed_final.path.lower()
    if final_host in SSO_LOGIN_HOSTS:
        return "login_required"
    if re.search(
        r"/(?:sso-)?(?:login|signin|cas/login|auth/logon)(?:/|$|\.)", path_lower, re.I
    ):
        return "login_required"
    html_no_scripts = SCRIPT_STYLE_RE.sub(" ", raw_html)
    if PASSWORD_INPUT_RE.search(html_no_scripts):
        return "login_required"
    if re.search(
        r"^(?:.*统一)?(?:身份)?(?:认证|登录|登陆)|统一认证|用户登录|账号登录|sign\s*in|log\s*in",
        title.strip(),
        re.I,
    ):
        return "login_required"
    if len(visible_text) < 400 and re.search(
        r"请先登录|扫码登录|账号密码登录|统一身份认证", visible_text
    ):
        return "login_required"

    # Accessible vs SPA shell detection
    if http_status is not None and 200 <= http_status < 400:
        if identity_match or cas_callback_seen:
            return "accessible_verified_or_page"
        has_spa_markers = bool(SPA_MARKERS_RE.search(raw_html))
        has_script = "<script" in raw_html.lower()
        if len(visible_text) < 200 and (has_spa_markers or has_script):
            return "spa_shell_needs_browser"
        if len(visible_text) >= 200:
            return "accessible_verified_or_page"
        if len(raw_html.strip()) == 0:
            return "unreachable_or_error"
        return "spa_shell_needs_browser"

    return "unreachable_or_error"


def screen_single_site(
    target: dict[str, Any],
    socks_port: int,
    timeout_sec: float,
    business: dict[str, Any],
    refresh: dict[str, Any],
    identities: set[str],
    sensitive_values: set[str],
) -> dict[str, Any]:
    t0 = time.monotonic()
    host = (target.get("host") or "").lower()
    entry_url = target.get("url") or target.get("entry_url") or f"https://{host}/"
    proxy_url = f"socks5://{ '127.0.0.1' }:{socks_port}"

    cookies = build_isolated_cookies(business, refresh, host)
    redirect_hosts: list[str] = []
    issued_cookie_names: set[str] = set()
    cas_callback_seen = False
    http_status: int | None = None
    final_url_raw = entry_url.split("#", 1)[0]
    raw_html = ""
    error_raw: str | None = None
    redirect_loop = False
    blocked_external = False
    blocked_external_host = ""

    def add_redirect_host(h: str) -> None:
        hl = (h or "").lower()
        if hl and hl not in redirect_hosts:
            redirect_hosts.append(hl)

    try:
        timeout = httpx.Timeout(timeout_sec, connect=min(6.0, timeout_sec))
        with httpx.Client(
            proxy=proxy_url,
            cookies=cookies,
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
            verify=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        ) as client:
            current_url = final_url_raw
            visited_url_counts: dict[str, int] = {}
            for _hop in range(MAX_REDIRECT_HOPS):
                if time.monotonic() - t0 > timeout_sec:
                    raise TimeoutError(f"Exceeded {timeout_sec}s per-site timeout")

                parsed_cur = urllib.parse.urlsplit(current_url)
                cur_host = (parsed_cur.hostname or "").lower()
                add_redirect_host(cur_host)
                if (
                    cur_host in SSO_LOGIN_HOSTS
                    or "/cas" in parsed_cur.path.lower()
                    or "ticket=" in parsed_cur.query.lower()
                ):
                    cas_callback_seen = True

                # Loop guard (ignoring volatile ticket param)
                norm_key = f"{parsed_cur.scheme}://{parsed_cur.netloc}{parsed_cur.path}"
                visited_url_counts[norm_key] = visited_url_counts.get(norm_key, 0) + 1
                if visited_url_counts[norm_key] > 3:
                    redirect_loop = True
                    error_raw = "redirect_loop"
                    break

                resp = client.get(current_url)
                http_status = resp.status_code
                final_url_raw = str(resp.url)

                for sc in resp.headers.get_list("set-cookie"):
                    cname = sc.split("=", 1)[0].strip()
                    if cname:
                        issued_cookie_names.add(cname)

                if resp.status_code in REDIRECT_STATUS_CODES:
                    loc = resp.headers.get("location", "").strip()
                    if not loc:
                        break
                    next_url = urllib.parse.urljoin(final_url_raw, loc).split("#", 1)[0]
                    parsed_next = urllib.parse.urlsplit(next_url)
                    if parsed_next.scheme.lower() not in ("http", "https"):
                        error_raw = f"unsupported_redirect_scheme:{parsed_next.scheme}"
                        break
                    next_host = (parsed_next.hostname or "").lower()
                    path_and_q = parsed_next.path + (
                        f"?{parsed_next.query}" if parsed_next.query else ""
                    )
                    if (
                        LOGOUT_PATH_RE.search(path_and_q)
                        or LOGOUT_PATH_RE.search(parsed_next.path)
                        or re.search(r"(^|/)(logout|signout|logoff)(/|$)", parsed_next.path, re.I)
                    ):
                        error_raw = "blocked_logout_redirect"
                        break
                    if not is_corp_domain(next_host):
                        add_redirect_host(next_host)
                        blocked_external = True
                        blocked_external_host = next_host
                        error_raw = f"blocked_external_redirect:{next_host}"
                        break
                    current_url = next_url
                    continue

                # Non-redirect terminal response
                ctype = (resp.headers.get("content-type") or "").lower()
                if "html" in ctype or "text" in ctype or "json" in ctype or not ctype:
                    raw_html = resp.text[:512 * 1024]
                break
            else:
                redirect_loop = True
                error_raw = "max_redirects_exceeded"
    except Exception as exc:
        error_raw = f"{type(exc).__name__}: {str(exc).splitlines()[0][:180]}"

    title_raw, visible_text = extract_title_and_visible_text(raw_html)
    identity_match = bool(
        raw_html and any(ident in raw_html for ident in identities)
    )

    san_entry_url, _ = sanitize_url_strict(entry_url, sensitive_values)
    san_final_url, final_host = sanitize_url_strict(final_url_raw, sensitive_values)
    if not san_final_url:
        san_final_url = san_entry_url
        final_host = host
    san_title = sanitize_text(title_raw, sensitive_values, max_len=120)
    san_error = sanitize_text(error_raw or "", sensitive_values, max_len=200) if error_raw else None

    verdict = classify_http_response(
        http_status=http_status,
        final_url=san_final_url,
        final_host=final_host,
        raw_html=raw_html,
        title=san_title,
        visible_text=visible_text,
        cas_callback_seen=cas_callback_seen,
        identity_match=identity_match,
        issued_cookie_names=sorted(issued_cookie_names),
        redirect_loop=redirect_loop,
        blocked_external=blocked_external,
        blocked_external_host=blocked_external_host,
        error_msg=san_error,
    )

    record = {
        "host": host,
        "name": target.get("name", host),
        "priority": target.get("priority", ""),
        "entry_url": san_entry_url,
        "final_url": san_final_url,
        "http_status": http_status,
        "title": san_title,
        "verdict": verdict,
        "identity_match": bool(identity_match),
        "cas_callback_seen": bool(cas_callback_seen),
        "issued_cookie_names": sorted(issued_cookie_names),
        "redirect_hosts": redirect_hosts,
        "html_bytes": len(raw_html.encode("utf-8", errors="replace")) if raw_html else 0,
        "error": san_error,
        "elapsed_seconds": round(time.monotonic() - t0, 2),
    }
    return recursive_scrub(record, sensitive_values)


def render_markdown(payload: dict[str, Any]) -> str:
    results: list[dict[str, Any]] = payload["results"]
    counts = payload["verdict_counts"]
    p1_counts = payload["by_priority"]["P1"]
    p2_counts = payload["by_priority"]["P2"]
    enter_list = [
        r
        for r in results
        if r["verdict"] in ("accessible_verified_or_page", "spa_shell_needs_browser")
    ]
    baseline = payload["baseline_probe"]

    lines: list[str] = [
        "# Phase 2B-1 P1/P2 轻量 HTTP/SSO 快筛报告（89 站）",
        "",
        f"> **采集时间（UTC）**：`{payload['generated_at_utc']}`",
        "> **执行通道**：托管临时 SSH SOCKS5 隧道（`try...finally` 退出释放，未触碰系统既有 8020 隧道）；并发 `max_workers="
        + f"{payload['max_workers']}`，单站超时 `{payload['timeout_sec']}s`；仅发 `GET`，全局拦截 `/logout|/signout|/logoff` 与非企业域外站重定向。",
        f"> **基线探活结果**：`{baseline['host']}` -> `verdict={baseline['verdict']}`、`http_status={baseline['http_status']}`、`cas_callback_seen={baseline['cas_callback_seen']}`（耗时 `{baseline['elapsed_seconds']}s`）",
        f"> **核心结论**：89 站中共有 **{len(enter_list)} 站** 筛出需进入 **Phase 2B-2 深度浏览器测绘**（其中 `accessible_verified_or_page` {counts.get('accessible_verified_or_page', 0)} 站、`spa_shell_needs_browser` {counts.get('spa_shell_needs_browser', 0)} 站），其余 **{len(results) - len(enter_list)} 站** 在轻量快筛阶段确认为无权限/需独立登录/不可达并跳过浏览器深扫。",
        "",
        "---",
        "",
        "## 1. 89 站快筛判定分布汇总",
        "",
        "| 判定分类 (`verdict`) | P1 (47站) | P2 (42站) | 合计 (89站) | 占比 | 是否进入 2B-2 |",
        "| --- | ---: | ---: | ---: | ---: | :---: |",
    ]
    verdict_order = [
        ("accessible_verified_or_page", "是"),
        ("spa_shell_needs_browser", "是"),
        ("denied_403", "否"),
        ("login_required", "否"),
        ("unreachable_or_error", "否"),
    ]
    total = len(results) or 1
    for v, enter_flag in verdict_order:
        c_all = counts.get(v, 0)
        c_p1 = p1_counts.get(v, 0)
        c_p2 = p2_counts.get(v, 0)
        pct = f"{(c_all / total) * 100:.1f}%"
        lines.append(f"| `{v}` | {c_p1} | {c_p2} | **{c_all}** | {pct} | {enter_flag} |")
    lines.append(f"| **合计** | **47** | **42** | **{len(results)}** | **100%** | **进入 2B-2：{len(enter_list)} 站** |")

    lines.extend(
        [
            "",
            "---",
            "",
            f"## 2. 拟进入 Phase 2B-2 深度浏览器测绘清单（共 {len(enter_list)} 站）",
            "",
            "| # | 优先级 | 主机 (`host`) | 系统名称 | 快筛判定 | HTTP | CAS | 入口 URL |",
            "| ---: | :---: | --- | --- | --- | ---: | :---: | --- |",
        ]
    )
    for idx, r in enumerate(enter_list, start=1):
        lines.append(
            f"| {idx} | {r['priority']} | `{r['host']}` | {r['name']} | `{r['verdict']}` | "
            f"{r['http_status'] or '—'} | `{str(r['cas_callback_seen']).lower()}` | `{r['entry_url']}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. 89 站逐站快筛全量明细",
            "",
            "| # | 优先级 | 主机 (`host`) | 系统名称 | 判定 (`verdict`) | HTTP | 页面标题 | 终态 URL | 签发 Cookie 数 | 耗时 | 备注/错误 |",
            "| ---: | :---: | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- |",
        ]
    )
    for idx, r in enumerate(results, start=1):
        title_s = (r["title"] or "—").replace("|", "\\|")
        err_s = (r["error"] or "—").replace("|", "\\|")
        lines.append(
            f"| {idx} | {r['priority']} | `{r['host']}` | {r['name'].replace('|', '\\|')} | `{r['verdict']}` | "
            f"{r['http_status'] or '—'} | {title_s} | `{r['final_url']}` | {len(r['issued_cookie_names'])} | "
            f"{r['elapsed_seconds']}s | {err_s} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. 临时隧道释放确认",
            "",
            f"- `tunnel_cleanup`: `{json.dumps(payload['tunnel_cleanup'], ensure_ascii=False)}`",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 2B-1 lightweight P1/P2 HTTP/SSO screener.")
    p.add_argument("--targets-json", type=Path, default=DEFAULT_TARGETS_JSON)
    p.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    p.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--timeout-sec", type=float, default=10.0)
    p.add_argument("--ssh-host", type=str, default=DEFAULT_SSH_HOST)
    p.add_argument("--remote-cred-path", type=str, default=DEFAULT_REMOTE_CRED_PATH)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    targets: list[dict[str, Any]] = json.loads(args.targets_json.read_text(encoding="utf-8"))
    business, refresh, identities, sensitive_values, _ = load_credentials_in_memory(
        ssh_host=args.ssh_host, remote_path=args.remote_cred_path
    )
    print(
        f"Loaded in-memory session material for HTTP screener (targets={len(targets)}, "
        f"business_cookies={len(business)}, has_tgc={bool(refresh.get('TGC'))})",
        flush=True,
    )

    results_by_idx: dict[int, dict[str, Any]] = {}
    tunnel_cleanup: dict[str, Any] = {}
    baseline_result: dict[str, Any] = {}

    try:
        with ManagedSocksTunnel(args.ssh_host) as tunnel:
            assert tunnel.port is not None
            baseline_result = screen_single_site(
                BASELINE_PROBE_TARGET,
                socks_port=tunnel.port,
                timeout_sec=args.timeout_sec,
                business=business,
                refresh=refresh,
                identities=identities,
                sensitive_values=sensitive_values,
            )
            print(
                f"[BASELINE] {baseline_result['host']} -> {baseline_result['verdict']} "
                f"(status={baseline_result['http_status']}, cas={baseline_result['cas_callback_seen']}, "
                f"{baseline_result['elapsed_seconds']}s)",
                flush=True,
            )
            if baseline_result["verdict"] not in (
                "accessible_verified_or_page",
                "spa_shell_needs_browser",
            ):
                raise RuntimeError(
                    f"Baseline liveness probe failed: verdict={baseline_result['verdict']}, "
                    f"error={baseline_result.get('error')}"
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
                future_to_idx = {
                    pool.submit(
                        screen_single_site,
                        t,
                        tunnel.port,
                        args.timeout_sec,
                        business,
                        refresh,
                        identities,
                        sensitive_values,
                    ): idx
                    for idx, t in enumerate(targets)
                }
                done_cnt = 0
                for fut in concurrent.futures.as_completed(future_to_idx):
                    idx = future_to_idx[fut]
                    rec = fut.result()
                    results_by_idx[idx] = rec
                    done_cnt += 1
                    print(
                        f"[{done_cnt}/{len(targets)}] ({rec['priority']}) {rec['host']} -> "
                        f"{rec['verdict']} (status={rec['http_status']}, cas={rec['cas_callback_seen']}, "
                        f"{rec['elapsed_seconds']}s)",
                        flush=True,
                    )
        tunnel_cleanup = tunnel.cleanup_status
    finally:
        business.clear()
        refresh.clear()
        sensitive_values.clear()

    ordered_results = [results_by_idx[i] for i in range(len(targets))]
    verdict_counts: dict[str, int] = {}
    by_priority: dict[str, dict[str, int]] = {"P1": {}, "P2": {}}
    for r in ordered_results:
        v = r["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        pr = r.get("priority") or "P1"
        by_priority.setdefault(pr, {})
        by_priority[pr][v] = by_priority[pr].get(v, 0) + 1

    enter_2b2_hosts = [
        r["host"]
        for r in ordered_results
        if r["verdict"] in ("accessible_verified_or_page", "spa_shell_needs_browser")
    ]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_targets": len(ordered_results),
        "max_workers": args.max_workers,
        "timeout_sec": args.timeout_sec,
        "baseline_probe": baseline_result,
        "verdict_counts": verdict_counts,
        "by_priority": by_priority,
        "enter_2b2_count": len(enter_2b2_hosts),
        "enter_2b2_hosts": enter_2b2_hosts,
        "tunnel_cleanup": tunnel_cleanup,
        "results": ordered_results,
    }

    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(render_markdown(payload), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "total_targets": len(ordered_results),
                "verdict_counts": verdict_counts,
                "enter_2b2_count": len(enter_2b2_hosts),
                "tunnel_cleanup": tunnel_cleanup,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
