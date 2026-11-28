#!/usr/bin/env python3
"""Phase 1 Hub Discovery & Gap Analysis (Read-Only, Redacted).

Reads existing authorized session material in memory from beike-server,
launches a temporary managed SOCKS5 tunnel via try...finally for intranet hubs,
visits the 7 aggregator hubs resolved from summary.json using Playwright,
intercepts all /logout|/signout|/logoff requests, extracts navigation/card/API
entries, performs offline diff analysis against the 28+15 baseline and historical
audit report, and writes redacted structured output to phase1_discovery.json.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Request,
    Response,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
SUMMARY_JSON_PATH = Path(
    WORKSPACE_ROOT.parent / "crm-sso-entry-review-2026-11-26" / "summary.json"
)
AUDIT_REPORT_PATH = Path(
    WORKSPACE_ROOT.parent / "kecom-security-audit-2026-11-20" / "kecom-security-audit-report-2026-11-20.md"
)
CRED_REUSE_PATH = Path(
    WORKSPACE_ROOT.parent / "ke-app-credential-reuse.md"
)
OUTPUT_JSON_PATH = WORKSPACE_ROOT / "phase1_discovery.json"

SSH_HOST_ALIAS = "beike-server"
REMOTE_CRED_PATH = "/var/lib/crm-connector/credential_store.bin"
HUB_HARD_TIMEOUT_SEC = 30.0
CIRCUIT_BREAKER_THRESHOLD = 3

CORP_ROOT_DOMAINS = (
    "ke.com",
    "lianjia.com",
    "deyoulife.com",
    "homelink.com.cn",
    "ljcdn.com",
    "beike.com",
)

SENSITIVE_QUERY_KEY_RE = re.compile(
    r"^(?:ticket|token|code|access_token|refresh_token|signature|sign|"
    r"credential|secret|password|pwd|auth|session|sessionid|jsessionid|"
    r"st|tgc|key|phone|mobile|email|ucid|usercode|employeeid|idcard)$",
    re.I,
)

LOGOUT_PATH_RE = re.compile(r"/(logout|signout|logoff)(/|$|\?)", re.I)

STATIC_ASSET_EXT_RE = re.compile(
    r"\.(?:js|mjs|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|otf|map|mp[34]|wasm)(?:$|\?)",
    re.I,
)

TELEMETRY_OR_STATIC_HOSTS = {
    "public-digc.ke.com",
    "public-h5-be-report.ke.com",
    "digc.ke.com",
    "s1.ljcdn.com",
    "s2.ljcdn.com",
    "s3.ljcdn.com",
    "img.ljcdn.com",
    "image1.ljcdn.com",
    "file.ljcdn.com",
}

PII_JSON_KEYS = {
    "user",
    "user_info",
    "userinfo",
    "employee",
    "employeev1",
    "loginuser",
    "bankcard",
    "empchildinfopolist",
    "eduexperiencev1list",
    "empworkexperiencev1s",
    "interestrelationshipv1s",
    "contractinfov1",
    "orginfov1list",
    "phone",
    "mobile",
    "homephone",
    "compphone",
    "email",
    "idcard",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "credential",
    "ticket",
    "ucid",
    "usercode",
    "realname",
    "employeename",
    "avatar",
}

PERSONAL_FIELD_KEYS = {
    "name",
    "realname",
    "username",
    "employeename",
    "nickname",
    "phone",
    "mobile",
    "homephone",
    "compphone",
    "email",
    "ucid",
    "usercode",
    "account",
    "employeeno",
    "employee_principal",
    "idcard",
    "bankcard",
}

KNOWN_AUTH_COOKIE_HINT_RE = re.compile(
    r"token|session|jsession|phpsessid|security_ticket|tgc|ssid|cas|auth|sid|login",
    re.I,
)

DOMAIN_HOST_RE = re.compile(
    r"(?<![*.\w-])([a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*\.(?:ke\.com|lianjia\.com|deyoulife\.com|homelink\.com\.cn|ljcdn\.com|beike\.com))\b",
    re.I,
)

HUB_SPEC_ORDER = [
    {
        "hub_key": "workbench",
        "hub_name": "工作台首页",
        "target_host": "shouye.link.lianjia.com",
        "public_direct_expected": False,
        "extra_paths": [],
    },
    {
        "hub_key": "newhouse_top_nav",
        "hub_name": "新房 / A+ 统一顶栏",
        "target_host": "link.fang.lianjia.com",
        "public_direct_expected": False,
        "extra_paths": [],
    },
    {
        "hub_key": "employee_portal",
        "hub_name": "员工门户",
        "target_host": "family.ke.com",
        "public_direct_expected": False,
        "extra_paths": [
            "https://family.ke.com/myInfo/profile",
            "https://family.ke.com/home/portal",
        ],
    },
    {
        "hub_key": "hr_portal",
        "hub_name": "HR 门户",
        "target_host": "hr.ke.com",
        "public_direct_expected": False,
        "extra_paths": [],
    },
    {
        "hub_key": "ai_portal",
        "hub_name": "AI 门户",
        "target_host": "letsai.ke.com",
        "public_direct_expected": True,
        "extra_paths": [],
    },
    {
        "hub_key": "help_center",
        "hub_name": "客服中心 / 业绩争议",
        "target_host": "helper.ke.com",
        "public_direct_expected": True,
        "extra_paths": [],
    },
    {
        "hub_key": "aplus_client_auth",
        "hub_name": "A+ 客户端认证服务",
        "target_host": "saas.a.ke.com",
        "public_direct_expected": False,
        "extra_paths": [],
    },
]


class CircuitBreakerTriggered(RuntimeError):
    """Raised when 3 consecutive hubs unexpectedly fall back to a login page."""


class ManagedSocksTunnel:
    """Manage a temporary SSH dynamic SOCKS5 tunnel via try...finally."""

    def __init__(self, host_alias: str = SSH_HOST_ALIAS) -> None:
        self.host_alias = host_alias
        self.port: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.cleanup_status: dict[str, Any] = {
            "started": False,
            "terminated": False,
            "port_released": False,
            "return_code": None,
        }

    @staticmethod
    def _pick_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _is_port_listening(port: int, timeout: float = 0.25) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

    def __enter__(self) -> "ManagedSocksTunnel":
        self.port = self._pick_free_port()
        cmd = [
            "ssh",
            "-N",
            "-D",
            f"127.0.0.1:{self.port}",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            self.host_alias,
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = b""
                if self.proc.stderr is not None:
                    err = self.proc.stderr.read(400)
                raise RuntimeError(
                    f"SSH tunnel exited early (code={self.proc.returncode}): "
                    f"{err.decode('utf-8', 'replace')[:120]}"
                )
            if self._is_port_listening(self.port):
                self.cleanup_status["started"] = True
                return self
            time.sleep(0.2)
        self.__exit__(None, None, None)
        raise TimeoutError("Timed out waiting for temporary SOCKS5 tunnel port")

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
                self.cleanup_status["return_code"] = self.proc.returncode
                self.cleanup_status["terminated"] = self.proc.poll() is not None
            finally:
                if self.proc.stderr is not None:
                    try:
                        self.proc.stderr.close()
                    except OSError:
                        pass

        if self.port is not None:
            deadline = time.monotonic() + 3.0
            released = False
            while time.monotonic() < deadline:
                if not self._is_port_listening(self.port, timeout=0.15):
                    released = True
                    break
                time.sleep(0.15)
            self.cleanup_status["port_released"] = released


def is_internal_corp_domain(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    return any(host == d or host.endswith(f".{d}") for d in CORP_ROOT_DOMAINS)


def sanitize_text(
    text: str, sensitive_values: set[str], max_len: int = 240
) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", str(text)).strip()
    for val in sorted(sensitive_values, key=len, reverse=True):
        if val and val in s:
            s = s.replace(val, "[REDACTED]")
    s = re.sub(r"\b(?:TGT?|ST)-[A-Za-z0-9._-]{6,}\b", "[REDACTED_TICKET]", s)
    s = re.sub(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{4,})?\b",
        "[REDACTED_JWT]",
        s,
    )
    s = re.sub(r"(?<![\dA-Za-z_-])1[3-9]\d{9}(?![\dA-Za-z_-])", "[REDACTED_PHONE]", s)
    s = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        s,
    )
    s = re.sub(
        r"(?<![\dA-Za-z])[1-6]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dA-Za-z])",
        "[REDACTED_ID]",
        s,
    )
    s = re.sub(
        r"(?<![\dA-Za-z.-])[3-6]\d{15,18}(?![\dA-Za-z.-])",
        "[REDACTED_CARD]",
        s,
    )
    s = re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])", "[REDACTED_HEX64]", s)
    s = re.sub(
        r"(?i)(?:[A-Za-z]:\\|/)[^\s\"'<>]*(?:(?:\.pem|\.key|\.p12|\.pfx)(?=$|[\s\"'<>?#:/])|id_rsa|id_ed25519|\.ssh[\\/][^\s\"'<>]+|credential_store\.bin|beike-store-media\.env)",
        "[REDACTED_PATH]",
        s,
    )
    return s[:max_len]


def _scrub_query_string(query: str, sensitive_values: set[str]) -> str:
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    cleaned: list[tuple[str, str]] = []
    for k, v in pairs:
        v_str = v or ""
        is_sensitive_val = (
            bool(SENSITIVE_QUERY_KEY_RE.match(k))
            or any(sv in v_str for sv in sensitive_values if sv)
            or bool(re.search(r"^(?:TGT?|ST)-|^eyJ[A-Za-z0-9_-]{8,}\.", v_str))
            or bool(re.match(r"^1[3-9]\d{9}$", v_str))
            or "@" in v_str
            or bool(re.match(r"^[0-9a-fA-F]{32,}$", v_str))
        )
        if is_sensitive_val:
            cleaned.append((k, "[REDACTED]"))
        else:
            cleaned.append((k, sanitize_text(v_str, sensitive_values, max_len=120)))
    return urllib.parse.urlencode(cleaned, safe="[]")


def sanitize_url(
    raw_url: str, sensitive_values: set[str], base_url: str = ""
) -> tuple[str, str]:
    if not raw_url:
        return "", ""
    u_str = str(raw_url).strip()
    if u_str.lower().startswith(("javascript:", "mailto:", "tel:", "data:")):
        return "", ""
    if base_url:
        u_str = urllib.parse.urljoin(base_url, u_str)
    parsed = urllib.parse.urlsplit(u_str)
    if parsed.scheme.lower() not in ("http", "https"):
        return "", ""
    host = (parsed.hostname or "").lower()
    clean_path = sanitize_text(parsed.path or "/", sensitive_values, max_len=300)
    clean_query = _scrub_query_string(parsed.query, sensitive_values)
    clean_frag = parsed.fragment or ""
    if clean_frag:
        if "=" in clean_frag and any(
            kw in clean_frag.lower()
            for kw in ("token", "ticket", "code", "secret", "auth", "session")
        ):
            clean_frag = re.sub(
                r"([A-Za-z0-9_.-]*(?:token|ticket|code|secret|auth|session)[A-Za-z0-9_.-]*=)[^&]+",
                r"\1[REDACTED]",
                clean_frag,
                flags=re.I,
            )
        clean_frag = sanitize_text(clean_frag, sensitive_values, max_len=200)
    netloc = host
    if parsed.port and not (
        (parsed.scheme == "http" and parsed.port == 80)
        or (parsed.scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{host}:{parsed.port}"
    sanitized = urllib.parse.urlunsplit(
        (parsed.scheme.lower(), netloc, clean_path, clean_query, clean_frag)
    )
    return sanitized, host


def load_credentials_in_memory() -> tuple[dict[str, Any], dict[str, Any], set[str], set[str]]:
    """Read credential_store.bin over SSH strictly into memory; never persist."""
    raw = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            SSH_HOST_ALIAS,
            "cat",
            REMOTE_CRED_PATH,
        ],
        timeout=20,
    )
    store = json.loads(raw.decode("utf-8"))
    del raw

    business = json.loads(base64.b64decode(store["credential_material_b64"]))
    refresh = json.loads(base64.b64decode(store["refresh_material_b64"]))

    identities = {
        str(v).strip()
        for v in (
            store.get("employee_principal"),
            business.get("UCID"),
            business.get("Lianjia_BUcid"),
        )
        if v and len(str(v).strip()) > 5
    }
    sensitive_values: set[str] = set(identities)
    for d in (business, refresh):
        for v in d.values():
            if isinstance(v, str) and len(v.strip()) >= 6:
                sensitive_values.add(v.strip())
    del store
    return business, refresh, identities, sensitive_values


def seed_context_cookies(
    context: BrowserContext, business: dict[str, Any], refresh: dict[str, Any]
) -> None:
    cookies: list[dict[str, Any]] = []
    for name, val in business.items():
        if isinstance(val, str) and val:
            for domain in (".link.lianjia.com", ".ke.com", ".lianjia.com"):
                cookies.append(
                    {
                        "name": name,
                        "value": val,
                        "domain": domain,
                        "path": "/",
                        "secure": False,
                        "httpOnly": False,
                    }
                )
    for name in ("TGC", "TGC_Secure"):
        val = refresh.get(name)
        if isinstance(val, str) and val:
            cookies.append(
                {
                    "name": name,
                    "value": val,
                    "domain": "login.ke.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            )
    context.add_cookies(cookies)


def resolve_hubs_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    by_host = {g.get("host"): g for g in summary.get("groups", []) if g.get("host")}
    resolved: list[dict[str, Any]] = []
    for spec in HUB_SPEC_ORDER:
        grp = by_host.get(spec["target_host"])
        if not grp:
            raise KeyError(
                f"Target hub host {spec['target_host']} not found in summary.json"
            )
        entries = grp.get("entries") or []
        entry_url = entries[0] if entries else grp.get("final", "")
        resolved.append(
            {
                "hub_key": spec["hub_key"],
                "hub_name": spec["hub_name"],
                "target_host": spec["target_host"],
                "entry_url": entry_url,
                "summary_final_url": grp.get("final", ""),
                "public_direct_expected": spec["public_direct_expected"],
                "extra_paths": spec["extra_paths"],
            }
        )
    return resolved


USER_CONTEXT_KEYS = {
    "user",
    "user_info",
    "userinfo",
    "employee",
    "employeev1",
    "loginuser",
}

ALWAYS_SENSITIVE_FIELD_KEYS = {
    "phone",
    "mobile",
    "homephone",
    "compphone",
    "email",
    "ucid",
    "usercode",
    "employeeno",
    "employee_principal",
    "idcard",
    "bankcard",
    "realname",
    "employeename",
}


def harvest_sensitive_and_check_identity(
    obj: Any,
    identities: set[str],
    sensitive_values: set[str],
    parent_key: str = "",
) -> bool:
    matched = False
    if isinstance(obj, dict):
        keys_lower = {str(k).lower() for k in obj.keys()}
        is_nav_like = bool(
            keys_lower
            & {"url", "link", "href", "jumpurl", "targeturl", "children", "submenus", "menus"}
        )
        has_user_markers = bool(
            keys_lower & {"ucid", "usercode", "mobile", "phone", "homephone", "orgcode", "positioncode", "postion"}
        )
        in_user_ctx = (parent_key in USER_CONTEXT_KEYS) or (has_user_markers and not is_nav_like)

        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                vs = str(v).strip()
                if vs in identities:
                    matched = True
                if kl in ALWAYS_SENSITIVE_FIELD_KEYS and vs:
                    if len(vs) >= 4 and not re.match(r"^[0-9]{1,5}$", vs):
                        sensitive_values.add(vs)
                elif in_user_ctx and kl in {"name", "username", "nickname", "account"} and vs:
                    if len(vs) >= 2 and not re.match(r"^[0-9]{1,5}$", vs):
                        sensitive_values.add(vs)
                elif kl in {"token", "access_token", "refresh_token", "credential", "ticket", "password"} and len(vs) >= 8:
                    sensitive_values.add(vs)
            if harvest_sensitive_and_check_identity(
                v, identities, sensitive_values, kl
            ):
                matched = True
    elif isinstance(obj, list):
        for item in obj:
            if harvest_sensitive_and_check_identity(
                item, identities, sensitive_values, parent_key
            ):
                matched = True
    elif isinstance(obj, str):
        if any(ident in obj for ident in identities):
            matched = True
    return matched


def extract_biz_code(data: Any) -> Any:
    if isinstance(data, dict):
        for key in ("code", "errno", "errCode", "retCode", "statusCode"):
            if key in data and isinstance(data[key], (int, str, bool)):
                return data[key]
    return None


def extract_links_from_json(
    obj: Any,
    api_path: str,
    base_url: str,
    sensitive_values: set[str],
    category_stack: list[str] | None = None,
    parent_key: str = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    stack = list(category_stack or [])

    if isinstance(obj, list):
        for item in obj:
            results.extend(
                extract_links_from_json(
                    item, api_path, base_url, sensitive_values, stack, parent_key
                )
            )
        return results

    if not isinstance(obj, dict):
        return results

    # Avoid descending into personal user profile blocks
    cur_label = ""
    for lk in (
        "menuName",
        "appName",
        "moduleName",
        "packageName",
        "title",
        "name",
        "label",
        "text",
        "navName",
        "tabName",
        "categoryName",
        "groupName",
    ):
        val = obj.get(lk)
        if isinstance(val, str) and val.strip():
            cur_label = sanitize_text(val, sensitive_values, max_len=80)
            break

    url_candidates: list[str] = []
    for uk in (
        "url",
        "link",
        "href",
        "jumpUrl",
        "targetUrl",
        "pcUrl",
        "webUrl",
        "appUrl",
        "redirectUrl",
        "menuUrl",
        "portalUrl",
        "linkUrl",
        "openUrl",
        "toUrl",
        "enterUrl",
        "homepage",
        "indexUrl",
        "webAddress",
        "pcLink",
        "route",
        "path",
    ):
        val = obj.get(uk)
        if isinstance(val, str) and val.strip():
            s = val.strip()
            if uk in ("route", "path") and not (
                s.startswith("http://") or s.startswith("https://") or s.startswith("/")
            ):
                continue
            url_candidates.append(s)

    for raw_u in url_candidates:
        sanitized, host = sanitize_url(raw_u, sensitive_values, base_url=base_url)
        if sanitized and host:
            cat = " > ".join(stack) if stack else api_path
            results.append(
                {
                    "source": "xhr_api",
                    "category": sanitize_text(cat, sensitive_values, max_len=100),
                    "label": cur_label or host,
                    "sanitized_url": sanitized,
                    "host": host,
                    "is_internal_corp_domain": is_internal_corp_domain(host),
                }
            )

    next_stack = list(stack)
    if cur_label and (
        any(
            ck in obj
            for ck in (
                "children",
                "subMenus",
                "childList",
                "items",
                "list",
                "menus",
                "apps",
                "modules",
                "packages",
                "myPackages",
            )
        )
    ):
        if len(next_stack) < 3 and cur_label not in next_stack:
            next_stack.append(cur_label)

    for k, v in obj.items():
        kl = str(k).lower()
        if kl in PII_JSON_KEYS:
            continue
        if isinstance(v, (dict, list)):
            child_stack = list(next_stack)
            if k in ("myPackages", "tenants", "nav", "topNav", "menus", "modules") and not child_stack:
                child_stack.append(k)
            results.extend(
                extract_links_from_json(
                    v, api_path, base_url, sensitive_values, child_stack, kl
                )
            )
    return results


DOM_EXTRACT_JS = r"""
() => {
  const results = [];
  const seen = new Set();

  function cleanText(s) {
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function isReasonableHeading(t) {
    if (!t || t.length > 45) return false;
    if (/安全退出|退出登录|AI布道师主页/.test(t)) return false;
    return true;
  }

  function findCategory(el) {
    let cur = el;
    for (let depth = 0; cur && depth < 8; depth++, cur = cur.parentElement) {
      // Check preceding siblings first (e.g. OneNav heading div right before .row of cards)
      let prev = cur.previousElementSibling;
      for (let p = 0; prev && p < 4; p++, prev = prev.previousElementSibling) {
        if (/^H[1-4]$/.test(prev.tagName)) {
          const t = cleanText(prev.innerText || prev.textContent);
          if (isReasonableHeading(t)) return t;
        }
        const hChild = prev.querySelector && prev.querySelector('h1, h2, h3, h4, [id^="term-"], .text-gray');
        if (hChild) {
          const t = cleanText(hChild.innerText || hChild.textContent);
          if (isReasonableHeading(t)) return t;
        }
        const pcls = typeof prev.className === 'string' ? prev.className : '';
        if (/title|header|category|d-flex/i.test(pcls)) {
          const t = cleanText(prev.innerText || prev.textContent);
          if (isReasonableHeading(t)) return t;
        }
      }

      const cls = typeof cur.className === 'string' ? cur.className : '';
      if (
        cur.tagName === 'SECTION' ||
        (cur.id && cur.id.startsWith('term-')) ||
        /group|section|category|module|block|panel|card-box|portal/i.test(cls)
      ) {
        const heading = cur.querySelector(
          ':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > .title, :scope > .section-title, :scope > .group-title, :scope > .category-title, :scope > [class*="title"], :scope > div > h2, :scope > div > h3, :scope > div > h4'
        );
        if (heading) {
          const t = cleanText(heading.innerText || heading.textContent);
          if (isReasonableHeading(t)) return t;
        }
      }
    }
    if (el.closest('header, [class*="header"], [class*="topbar"], [class*="top-nav"]')) return 'top_nav';
    if (el.closest('aside, [class*="sidebar"], [class*="side-nav"], [class*="left"]')) return 'side_nav';
    if (el.closest('nav, [role="navigation"], [class*="menu"], [class*="nav"]')) return 'nav';
    return 'page_body';
  }

  function inferSource(el) {
    const cls = typeof el.className === 'string' ? el.className : '';
    if (
      el.closest('[class*="card"], [class*="app"], [class*="portal"], [class*="grid"], [id^="term-"], .url-card') ||
      /card|app|item|tile/i.test(cls)
    ) {
      return 'dom_card';
    }
    return 'dom_nav';
  }

  function extractLabel(el) {
    const titleEl = el.querySelector('[class*="title"], [class*="name"], strong, h3, h4, .text-sm, .overflowClip_1');
    const raw = titleEl ? (titleEl.innerText || titleEl.textContent) : (el.innerText || el.textContent);
    let txt = cleanText(raw);
    if (!txt) {
      txt = cleanText(el.getAttribute('title') || el.getAttribute('aria-label') || el.getAttribute('data-title') || '');
    }
    if (!txt) {
      const img = el.querySelector('img[alt]');
      if (img) txt = cleanText(img.getAttribute('alt'));
    }
    return txt.slice(0, 80);
  }

  function walkRoots(root, linkList, shadowStats) {
    const nodes = root.querySelectorAll('a[href], [data-url], [data-href], [data-link]');
    for (const el of nodes) {
      linkList.push(el);
    }
    const allEls = root.querySelectorAll('*');
    for (const el of allEls) {
      if (el.shadowRoot) {
        shadowStats.shadowRoots += 1;
        shadowStats.shadowText += ' ' + cleanText(el.shadowRoot.textContent || '').slice(0, 600);
        walkRoots(el.shadowRoot, linkList, shadowStats);
      }
    }
  }

  const collectedEls = [];
  const shadowStats = { shadowRoots: 0, shadowText: '' };
  walkRoots(document, collectedEls, shadowStats);

  for (const el of collectedEls) {
    const attrs = ['data-url', 'data-href', 'data-link', 'href'];
    for (const attr of attrs) {
      const rawUrl = el.getAttribute(attr);
      if (!rawUrl) continue;
      const trimmed = rawUrl.trim();
      if (!trimmed || trimmed === '#' || trimmed.startsWith('javascript:')) continue;
      if (trimmed.startsWith('#') && !trimmed.startsWith('#/')) continue;
      const label = extractLabel(el);
      const source = inferSource(el);
      const category = findCategory(el);
      const key = `${source}|${category}|${label}|${trimmed}`;
      if (seen.has(key)) continue;
      seen.add(key);
      results.push({ source, category, label, raw_url: trimmed });
    }
  }

  const passwordInputs = document.querySelectorAll('input[type="password"]').length;
  const bodyText = cleanText((document.body ? document.body.innerText : '') + ' ' + shadowStats.shadowText).slice(0, 4000);
  const inlineScripts = Array.from(document.querySelectorAll('script:not([src])')).map(s => cleanText(s.textContent || '').slice(0, 120)).filter(Boolean);
  return {
    links: results,
    password_inputs: passwordInputs,
    body_text_sample: bodyText.slice(0, 300),
    body_text_full_len: bodyText.length,
    body_text_for_match: bodyText,
    link_count: collectedEls.length,
    shadow_root_count: shadowStats.shadowRoots,
    html_len: document.documentElement ? document.documentElement.outerHTML.length : 0,
    inline_script_count: inlineScripts.length,
    inline_script_previews: inlineScripts.slice(0, 3),
    script_srcs: Array.from(document.querySelectorAll('script[src]')).map(s => s.getAttribute('src')).filter(Boolean).slice(0, 30),
  };
}
"""


def probe_hub_in_context(
    context: BrowserContext,
    hub: dict[str, Any],
    identities: set[str],
    sensitive_values: set[str],
) -> tuple[dict[str, Any], bool]:
    urls_to_visit = [hub["entry_url"]] + [
        u for u in hub.get("extra_paths", []) if u != hub["entry_url"]
    ]
    api_endpoints: dict[tuple[str, str, str, int, str], dict[str, Any]] = {}
    extracted_links_map: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    errors: list[str] = []
    blocked_logout_count = 0
    identity_matched = False
    issued_cookie_names: set[str] = set()
    primary_http_status: int | None = None
    primary_final_url = ""
    primary_title = ""
    page_shapes: list[dict[str, Any]] = []
    is_login_fallback = False
    is_denied = False
    sub_pages_visited: list[str] = []

    def handle_route(route: Route, request: Request) -> None:
        nonlocal blocked_logout_count
        parsed = urllib.parse.urlsplit(request.url)
        path_and_q = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if (
            LOGOUT_PATH_RE.search(path_and_q)
            or LOGOUT_PATH_RE.search(parsed.path)
            or re.search(r"(^|/)(logout|signout|logoff)(/|$)", parsed.path, re.I)
        ):
            blocked_logout_count += 1
            route.abort()
            return
        route.continue_()

    context.route("**/*", handle_route)
    page: Page = context.new_page()

    def on_response(resp: Response) -> None:
        nonlocal identity_matched
        try:
            for hdr in resp.headers_array():
                if hdr.get("name", "").lower() == "set-cookie":
                    cname = hdr.get("value", "").split("=", 1)[0].strip()
                    if cname:
                        issued_cookie_names.add(cname)

            req = resp.request
            r_url = resp.url
            parsed = urllib.parse.urlsplit(r_url)
            host = (parsed.hostname or "").lower()
            path = parsed.path or "/"
            res_type = req.resource_type
            if res_type not in ("xhr", "fetch", "document") and STATIC_ASSET_EXT_RE.search(r_url):
                return
            if res_type in ("stylesheet", "image", "font", "media"):
                return

            ctype = (resp.headers.get("content-type") or "").lower()
            biz_code: Any = None
            if res_type in ("xhr", "fetch") or "json" in ctype:
                if "json" in ctype or "text" in ctype or not ctype:
                    try:
                        body_bytes = resp.body()
                    except Exception:
                        body_bytes = b""
                    if body_bytes and len(body_bytes) <= 2 * 1024 * 1024:
                        body_text = body_bytes.decode("utf-8", errors="replace")
                        if any(ident in body_text for ident in identities):
                            identity_matched = True
                        stripped = body_text.lstrip()
                        if stripped.startswith("{") or stripped.startswith("["):
                            try:
                                data = json.loads(stripped)
                                if harvest_sensitive_and_check_identity(
                                    data, identities, sensitive_values
                                ):
                                    identity_matched = True
                                biz_code = extract_biz_code(data)
                                if is_internal_corp_domain(host) and host not in TELEMETRY_OR_STATIC_HOSTS:
                                    for item in extract_links_from_json(
                                        data,
                                        path,
                                        f"{parsed.scheme}://{host}",
                                        sensitive_values,
                                    ):
                                        dedup_k = (
                                            item["source"],
                                            item["category"],
                                            item["label"],
                                            item["sanitized_url"],
                                        )
                                        extracted_links_map[dedup_k] = item
                            except json.JSONDecodeError:
                                pass

            if res_type in ("xhr", "fetch") or "json" in ctype:
                ep_key = (req.method, host, path, resp.status, str(biz_code))
                api_endpoints[ep_key] = {
                    "method": req.method,
                    "host": host,
                    "path": sanitize_text(path, sensitive_values, max_len=240),
                    "status": resp.status,
                    "biz_code": biz_code,
                }
        except Exception:
            pass

    page.on("response", on_response)

    try:
        for idx, target_url in enumerate(urls_to_visit):
            if idx > 0 and target_url in sub_pages_visited:
                continue
            deadline = time.monotonic() + HUB_HARD_TIMEOUT_SEC
            try:
                rem_ms = max(2000, int((deadline - time.monotonic()) * 1000))
                resp = page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=min(22000, rem_ms),
                )
                status = resp.status if resp is not None else None
                try:
                    rem_ms = max(500, int((deadline - time.monotonic()) * 1000))
                    page.wait_for_load_state(
                        "networkidle", timeout=min(7000, rem_ms)
                    )
                except PlaywrightTimeoutError:
                    pass
                rem_ms = int((deadline - time.monotonic()) * 1000)
                if rem_ms > 400:
                    page.wait_for_timeout(min(2500, rem_ms))

                cur_final_url, cur_final_host = sanitize_url(
                    page.url, sensitive_values
                )
                sub_pages_visited.append(cur_final_url)
                cur_title = sanitize_text(page.title(), sensitive_values, max_len=160)

                total_pw_inputs = 0
                combined_body_match = ""
                for frame in page.frames:
                    try:
                        frame_data = frame.evaluate(DOM_EXTRACT_JS)
                    except Exception:
                        continue
                    total_pw_inputs += int(frame_data.get("password_inputs") or 0)
                    body_for_match = frame_data.get("body_text_for_match") or ""
                    if any(ident in body_for_match for ident in identities):
                        identity_matched = True
                    combined_body_match += " " + body_for_match[:1500]
                    for raw_item in frame_data.get("links", []):
                        san_u, link_host = sanitize_url(
                            raw_item.get("raw_url", ""),
                            sensitive_values,
                            base_url=frame.url,
                        )
                        if not san_u or not link_host:
                            continue
                        if LOGOUT_PATH_RE.search(san_u):
                            continue
                        label = sanitize_text(
                            raw_item.get("label", ""), sensitive_values, max_len=100
                        )
                        if not label:
                            label = link_host
                        cat = sanitize_text(
                            raw_item.get("category", "page_body"),
                            sensitive_values,
                            max_len=80,
                        )
                        src = raw_item.get("source") or "dom_nav"
                        item = {
                            "source": src,
                            "category": cat,
                            "label": label,
                            "sanitized_url": san_u,
                            "host": link_host,
                            "is_internal_corp_domain": is_internal_corp_domain(
                                link_host
                            ),
                        }
                        dedup_k = (src, cat, label, san_u)
                        extracted_links_map[dedup_k] = item

                    if frame == page.main_frame:
                        page_shapes.append(
                            {
                                "url": cur_final_url,
                                "body_text_len": frame_data.get("body_text_full_len", 0),
                                "body_text_preview": sanitize_text(
                                    frame_data.get("body_text_sample", ""),
                                    sensitive_values,
                                    max_len=180,
                                ),
                                "dom_anchor_count": frame_data.get("link_count", 0),
                                "shadow_root_count": frame_data.get("shadow_root_count", 0),
                                "html_len": frame_data.get("html_len", 0),
                                "inline_script_count": frame_data.get("inline_script_count", 0),
                                "inline_script_previews": [
                                    sanitize_text(p, sensitive_values, max_len=120)
                                    for p in frame_data.get("inline_script_previews", [])
                                ],
                                "script_srcs": [
                                    sanitize_url(s, sensitive_values, base_url=frame.url)[0]
                                    for s in frame_data.get("script_srcs", [])
                                ],
                            }
                        )

                cur_path = urllib.parse.urlsplit(cur_final_url).path.lower()
                cur_login = (
                    cur_final_host in {"login.ke.com", "login.lianjia.com", "auth.lianjia.com"}
                    or bool(re.search(r"/(login|signin|sso-login)(/|$)", cur_path, re.I))
                    or bool(re.search(r"登录|登陆|sign.?in|log.?in", cur_title, re.I))
                    or total_pw_inputs > 0
                )
                cur_denied = (
                    (status in (401, 403))
                    or cur_final_host == "hip.lianjia.com"
                    or "/forbidden" in cur_path
                    or cur_title.strip().lower() == "forbidden"
                    or "无权限" in cur_title
                )
                if idx == 0:
                    primary_http_status = status
                    primary_final_url = cur_final_url
                    primary_title = cur_title
                    is_login_fallback = cur_login
                    is_denied = cur_denied
            except Exception as exc:
                errors.append(
                    sanitize_text(
                        f"{type(exc).__name__}: {str(exc).splitlines()[0][:180]}",
                        sensitive_values,
                    )
                )

        cookie_names: set[str] = set(issued_cookie_names)
        for c in context.cookies():
            cname = c.get("name", "")
            cdom = (c.get("domain") or "").lstrip(".").lower()
            if not cname:
                continue
            if cdom and (
                hub["target_host"] == cdom
                or hub["target_host"].endswith(f".{cdom}")
                or cdom.endswith(hub["target_host"])
            ):
                if cname not in business_cookie_names_global:
                    cookie_names.add(cname)

        authenticated = (
            primary_http_status is not None
            and 200 <= primary_http_status < 400
            and not is_login_fallback
            and not is_denied
            and len(errors) == 0
        )

        record = {
            "hub_key": hub["hub_key"],
            "hub_name": hub["hub_name"],
            "entry_url": hub["entry_url"],
            "sub_pages_visited": sub_pages_visited,
            "final_url": primary_final_url or hub["entry_url"],
            "title": primary_title,
            "http_status": primary_http_status,
            "authenticated": authenticated,
            "identity_match": identity_matched,
            "auth_cookies_issued": sorted(cookie_names),
            "observed_api_endpoints": sorted(
                api_endpoints.values(),
                key=lambda x: (x["host"], x["path"], x["method"]),
            ),
            "extracted_links": sorted(
                extracted_links_map.values(),
                key=lambda x: (x["source"], x["category"], x["host"], x["label"]),
            ),
            "page_shapes": page_shapes,
            "blocked_logout_requests": blocked_logout_count,
            "errors": errors,
        }
        return record, is_login_fallback
    finally:
        try:
            page.close()
        except Exception:
            pass


business_cookie_names_global: set[str] = set()


def parse_historical_hosts() -> dict[str, set[str]]:
    """Parse documented hosts from summary.json and historical audit docs."""
    summary = json.loads(SUMMARY_JSON_PATH.read_text(encoding="utf-8"))
    confirmed_28: set[str] = set()
    alias_to_confirmed: dict[str, str] = {}
    proof_api_hosts: set[str] = set()

    for grp in summary.get("groups", []):
        host = (grp.get("host") or "").strip().lower()
        if not host:
            continue
        confirmed_28.add(host)
        for ent in grp.get("entries", []):
            ehost = (urllib.parse.urlsplit(ent).hostname or "").lower()
            if ehost and ehost != host:
                alias_to_confirmed[ehost] = host
        for p_list_name in ("proofs", "observed_api_results"):
            for pr in grp.get(p_list_name, []) or []:
                phost = (urllib.parse.urlsplit(pr.get("url", "")).hostname or "").lower()
                if phost and phost not in confirmed_28:
                    proof_api_hosts.add(phost)

    not_counted_by_verdict: dict[str, dict[str, Any]] = {}
    not_counted_final_hosts: set[str] = set()
    for nc in summary.get("not_counted", []):
        u = nc.get("url", "")
        nhost = (urllib.parse.urlsplit(u).hostname or "").lower()
        fhost = (urllib.parse.urlsplit(nc.get("final", "")).hostname or "").lower()
        if nhost:
            not_counted_by_verdict[nhost] = {
                "name": nc.get("name", ""),
                "verdict": nc.get("verdict", ""),
                "url": u,
                "final": nc.get("final", ""),
            }
        if fhost and fhost != nhost:
            not_counted_final_hosts.add(fhost)

    audit_hosts: set[str] = set()
    for doc_path in (AUDIT_REPORT_PATH, CRED_REUSE_PATH):
        if doc_path.exists():
            txt = doc_path.read_text(encoding="utf-8")
            for m in DOMAIN_HOST_RE.finditer(txt):
                h = m.group(1).lower()
                if "*" not in h:
                    audit_hosts.add(h)
    audit_hosts.update(proof_api_hosts)
    # Explicit documented subdomain wildcard in audit report
    audit_hosts.add("*.ttb.test.ke.com")
    audit_hosts.discard("ttb.test.ke.com")

    return {
        "confirmed_28": confirmed_28,
        "alias_to_confirmed": alias_to_confirmed,
        "not_counted_by_verdict": not_counted_by_verdict,
        "not_counted_final_hosts": not_counted_final_hosts,
        "audit_hosts": audit_hosts,
    }


def build_diff_analysis(
    hub_records: list[dict[str, Any]], baseline: dict[str, Any]
) -> dict[str, Any]:
    confirmed_28: set[str] = baseline["confirmed_28"]
    alias_to_confirmed: dict[str, str] = baseline["alias_to_confirmed"]
    not_counted_by_verdict: dict[str, dict[str, Any]] = baseline["not_counted_by_verdict"]
    not_counted_final_hosts: set[str] = baseline["not_counted_final_hosts"]
    audit_hosts: set[str] = baseline["audit_hosts"]

    discovered_info: dict[str, dict[str, Any]] = {}

    def ensure_host(host: str) -> dict[str, Any]:
        if host not in discovered_info:
            discovered_info[host] = {
                "host": host,
                "is_internal_corp_domain": is_internal_corp_domain(host),
                "seen_in_hubs": set(),
                "sources": set(),
                "categories": set(),
                "labels": set(),
                "sample_urls": set(),
                "in_historical_audit": host in audit_hosts,
            }
        return discovered_info[host]

    for rec in hub_records:
        hk = rec["hub_key"]
        fhost = (urllib.parse.urlsplit(rec.get("final_url", "")).hostname or "").lower()
        if fhost:
            info = ensure_host(fhost)
            info["seen_in_hubs"].add(hk)
            info["sources"].add("hub_final")
            info["labels"].add(rec["hub_name"])
            info["sample_urls"].add(rec["final_url"])
        for link in rec.get("extracted_links", []):
            lh = (link.get("host") or "").lower()
            if not lh:
                continue
            info = ensure_host(lh)
            info["seen_in_hubs"].add(hk)
            info["sources"].add(link["source"])
            if link.get("category"):
                info["categories"].add(link["category"])
            if link.get("label"):
                info["labels"].add(link["label"])
            if len(info["sample_urls"]) < 3:
                info["sample_urls"].add(link["sanitized_url"])
        for ep in rec.get("observed_api_endpoints", []):
            eh = (ep.get("host") or "").lower()
            if not eh:
                continue
            info = ensure_host(eh)
            info["seen_in_hubs"].add(hk)
            info["sources"].add("observed_api")
            if len(info["sample_urls"]) < 3:
                info["sample_urls"].add(f"https://{eh}{ep['path']}")

    # Include audit_hosts so category 2 captures all documented non-28 core backends/gateways/lite platforms
    for ah in sorted(audit_hosts):
        if ah in confirmed_28 or ah in alias_to_confirmed or ah in not_counted_by_verdict:
            continue
        if "*" in ah:
            info = ensure_host(ah)
            info["sources"].add("historical_audit_doc")
        else:
            info = ensure_host(ah)
            info["sources"].add("historical_audit_doc")

    # Also include 28-group entry aliases even if not clicked in hubs
    for al_host in sorted(alias_to_confirmed):
        info = ensure_host(al_host)
        info["sources"].add("summary_group_entry_alias")

    cat1_new_internal_web: list[dict[str, Any]] = []
    cat2_documented_core_backend_gateway: list[dict[str, Any]] = []
    cat3_reappeared_page_only_unverified: list[dict[str, Any]] = []
    cat4_aliases_of_28: list[dict[str, Any]] = []
    cat5_external_saas: list[dict[str, Any]] = []
    other_not_counted_reappeared: list[dict[str, Any]] = []
    confirmed_28_reappeared: list[str] = []

    for host in sorted(discovered_info):
        raw = discovered_info[host]
        entry = {
            "host": host,
            "seen_in_hubs": sorted(raw["seen_in_hubs"]),
            "sources": sorted(raw["sources"]),
            "categories": sorted(raw["categories"])[:10],
            "labels": sorted(raw["labels"])[:10],
            "sample_urls": sorted(raw["sample_urls"])[:3],
            "in_historical_audit": raw["in_historical_audit"],
        }

        if host in confirmed_28:
            if raw["seen_in_hubs"]:
                confirmed_28_reappeared.append(host)
            continue

        if host in alias_to_confirmed:
            entry["alias_of"] = alias_to_confirmed[host]
            cat4_aliases_of_28.append(entry)
            continue

        if host in not_counted_by_verdict:
            nc_meta = not_counted_by_verdict[host]
            entry["not_counted_verdict"] = nc_meta["verdict"]
            entry["not_counted_name"] = nc_meta["name"]
            if nc_meta["verdict"] == "page_only_unverified":
                cat3_reappeared_page_only_unverified.append(entry)
            else:
                other_not_counted_reappeared.append(entry)
            continue

        if not raw["is_internal_corp_domain"]:
            cat5_external_saas.append(entry)
            continue

        # Internal corp domain not in confirmed_28, not alias, not in not_counted_15
        only_api_or_telemetry = (
            raw["sources"].issubset({"observed_api", "historical_audit_doc"})
            or host in TELEMETRY_OR_STATIC_HOSTS
            or host in not_counted_final_hosts
            or host.startswith(("login.", "auth.", "hip.", "imapi.", "prd-assistant-backend."))
        )
        if raw["in_historical_audit"] and only_api_or_telemetry:
            cat2_documented_core_backend_gateway.append(entry)
        elif raw["in_historical_audit"] and not raw["seen_in_hubs"]:
            cat2_documented_core_backend_gateway.append(entry)
        elif host in TELEMETRY_OR_STATIC_HOSTS or (
            raw["sources"] == {"observed_api"}
        ):
            # Newly observed API / telemetry host (not a navigated web UI entry)
            entry["note"] = "observed_api_or_static_host"
            cat2_documented_core_backend_gateway.append(entry)
        else:
            cat1_new_internal_web.append(entry)

    return {
        "baseline_counts": {
            "confirmed_28": len(confirmed_28),
            "confirmed_28_entry_aliases": len(alias_to_confirmed),
            "not_counted_15": len(not_counted_by_verdict),
            "audit_documented_hosts": len(audit_hosts),
        },
        "confirmed_28_seen_in_hubs": sorted(confirmed_28_reappeared),
        "categories": {
            "1_new_internal_web_candidates": cat1_new_internal_web,
            "2_documented_core_backend_gateway_or_lite": cat2_documented_core_backend_gateway,
            "3_reappeared_page_only_unverified": cat3_reappeared_page_only_unverified,
            "4_aliases_of_confirmed_28": cat4_aliases_of_28,
            "5_external_third_party_saas": cat5_external_saas,
        },
        "other_not_counted_seen_in_hubs": other_not_counted_reappeared,
    }


def recursive_scrub(obj: Any, sensitive_values: set[str]) -> Any:
    if isinstance(obj, str):
        return sanitize_text(obj, sensitive_values, max_len=1000)
    if isinstance(obj, list):
        return [recursive_scrub(item, sensitive_values) for item in obj]
    if isinstance(obj, dict):
        return {k: recursive_scrub(v, sensitive_values) for k, v in obj.items()}
    return obj


def main() -> int:
    summary = json.loads(SUMMARY_JSON_PATH.read_text(encoding="utf-8"))
    hubs = resolve_hubs_from_summary(summary)
    baseline = parse_historical_hosts()

    business, refresh, identities, sensitive_values = load_credentials_in_memory()
    global business_cookie_names_global
    business_cookie_names_global = set(business.keys())

    hub_records: list[dict[str, Any]] = []
    tunnel_status: dict[str, Any] = {}
    consecutive_login_fallbacks = 0

    try:
        with sync_playwright() as p:
            public_hubs = [h for h in hubs if h["public_direct_expected"]]
            intranet_hubs = [h for h in hubs if not h["public_direct_expected"]]

            # 1. Probe public hubs directly first to verify direct reachability
            direct_browser: Browser | None = None
            direct_failed_hubs: list[dict[str, Any]] = []
            try:
                direct_browser = p.chromium.launch(headless=True, channel="chrome")
                for hub in public_hubs:
                    ctx = direct_browser.new_context(ignore_https_errors=False)
                    try:
                        seed_context_cookies(ctx, business, refresh)
                        rec, is_login = probe_hub_in_context(
                            ctx, hub, identities, sensitive_values
                        )
                    finally:
                        ctx.close()

                    if rec["http_status"] is not None and len(rec["errors"]) == 0:
                        rec["access_mode"] = "direct"
                        rec["direct_reachable"] = True
                        hub_records.append(rec)
                        if is_login:
                            consecutive_login_fallbacks += 1
                            if consecutive_login_fallbacks >= CIRCUIT_BREAKER_THRESHOLD:
                                raise CircuitBreakerTriggered(
                                    "Circuit breaker triggered: 3 consecutive hubs fell back to login page"
                                )
                        else:
                            consecutive_login_fallbacks = 0
                    else:
                        hub["_direct_errors"] = rec["errors"]
                        direct_failed_hubs.append(hub)
            finally:
                if direct_browser is not None:
                    direct_browser.close()

            # 2. Visit intranet hubs (and any direct-failed public hubs) via managed SOCKS5 tunnel
            tunnel_targets = intranet_hubs + direct_failed_hubs
            with ManagedSocksTunnel(SSH_HOST_ALIAS) as tunnel:
                tun_browser = p.chromium.launch(
                    headless=True,
                    channel="chrome",
                    proxy={"server": f"socks5://127.0.0.1:{tunnel.port}"},
                    args=[
                        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1, EXCLUDE localhost"
                    ],
                )
                try:
                    for hub in tunnel_targets:
                        ctx = tun_browser.new_context(ignore_https_errors=False)
                        try:
                            seed_context_cookies(ctx, business, refresh)
                            rec, is_login = probe_hub_in_context(
                                ctx, hub, identities, sensitive_values
                            )
                        finally:
                            ctx.close()
                        rec["access_mode"] = "socks5_tunnel"
                        rec["direct_reachable"] = (
                            False if hub in direct_failed_hubs else None
                        )
                        if hub.get("_direct_errors"):
                            rec["direct_probe_errors"] = hub["_direct_errors"]
                        hub_records.append(rec)
                        if is_login:
                            consecutive_login_fallbacks += 1
                            if consecutive_login_fallbacks >= CIRCUIT_BREAKER_THRESHOLD:
                                raise CircuitBreakerTriggered(
                                    "Circuit breaker triggered: 3 consecutive hubs fell back to login page"
                                )
                        else:
                            consecutive_login_fallbacks = 0
                finally:
                    tun_browser.close()
            tunnel_status = tunnel.cleanup_status
    finally:
        # Clear credential dicts from memory
        business.clear()
        refresh.clear()

    # Keep original 1..7 spec order
    order_idx = {spec["hub_key"]: i for i, spec in enumerate(HUB_SPEC_ORDER)}
    hub_records.sort(key=lambda r: order_idx.get(r["hub_key"], 99))

    diff_analysis = build_diff_analysis(hub_records, baseline)

    payload = recursive_scrub(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "phase": "phase1_hub_discovery",
            "tunnel_cleanup": tunnel_status,
            "hubs": hub_records,
            "diff_analysis": diff_analysis,
        },
        sensitive_values,
    )

    OUTPUT_JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "hub_count": len(hub_records),
                "authenticated_count": sum(1 for h in hub_records if h["authenticated"]),
                "new_internal_web_candidates": len(
                    diff_analysis["categories"]["1_new_internal_web_candidates"]
                ),
                "tunnel_cleanup": tunnel_status,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
