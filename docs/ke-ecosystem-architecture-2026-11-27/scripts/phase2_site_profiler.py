#!/usr/bin/env python3
"""Reusable Playwright Batch Site Profiler for Phase 2A (direct) and Phase 2B (tunnel).

Features:
- CLI: --targets-json, --output-jsonl, --resume, --use-tunnel, --timeout-ms
- Memory-only credential loading via short-lived read-only SSH command (never persisted)
- Optional managed temporary SSH SOCKS5 tunnel via try...finally (--use-tunnel)
- Isolated BrowserContext per target site
- Global interception & abort of /(logout|signout|logoff)(/|$|\\?)
- Multi-mode auth assembly (default CAS+cookie, Wandou localStorage.token, Moma CAS+Supabase/Falcon)
- Strict PII & static/telemetry filtering, recursive scrubbing, line-by-line flush
- Baseline circuit breaker (3 consecutive baseline sites falling back to login)
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import re
import socket
import subprocess
import sys
import threading
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

DEFAULT_SSH_HOST = "beike-server"
DEFAULT_REMOTE_CRED_PATH = "/var/lib/crm-connector/credential_store.bin"
DEFAULT_TIMEOUT_MS = 25000
CIRCUIT_BREAKER_THRESHOLD = 3

BASELINE_HOSTS = {
    "helper.ke.com",
    "letsai.ke.com",
    "moma.ke.com",
    "wandou.ke.com",
    "listing-de.ke.com",
}

CORP_ROOT_DOMAINS = (
    "ke.com",
    "lianjia.com",
    "deyoulife.com",
    "homelink.com.cn",
    "ljcdn.com",
    "beike.com",
)

LOGOUT_PATH_RE = re.compile(r"/(logout|signout|logoff)(/|$|\?)", re.I)

STATIC_ASSET_EXT_RE = re.compile(
    r"\.(?:js|mjs|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|otf|map|mp[34]|wasm|pdf)(?:$|\?)",
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
    "miao.baidu.com",
    "hm.baidu.com",
}

SENSITIVE_QUERY_KEY_RE = re.compile(
    r"^(?:ticket|token|code|access_token|refresh_token|signature|sign|"
    r"credential|secret|password|pwd|auth|session|sessionid|jsessionid|"
    r"st|tgc|key|phone|mobile|email|ucid|usercode|employeeid|idcard)$",
    re.I,
)

USER_CONTEXT_KEYS = {
    "user",
    "user_info",
    "userinfo",
    "employee",
    "employeev1",
    "loginuser",
    "currentuser",
    "me",
    "profile",
}

ALWAYS_SENSITIVE_FIELD_KEYS = {
    "phone",
    "mobile",
    "homephone",
    "compphone",
    "telephone",
    "tel",
    "email",
    "mail",
    "ucid",
    "usercode",
    "employeeno",
    "employeeid",
    "employee_principal",
    "empno",
    "idcard",
    "idnumber",
    "bankcard",
    "realname",
    "employeename",
    "truename",
    "orgname",
    "shopname",
    "storename",
    "deptname",
    "departmentname",
}

PERSONAL_CONTEXT_NAME_KEYS = {
    "name",
    "username",
    "nickname",
    "account",
    "displayname",
    "cname",
    "ename",
}

DIRECT_BLOCKED_ERR_MARKERS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_TIMED_OUT",
    "ERR_EMPTY_RESPONSE",
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_SSL_VERSION_OR_CIPHER_MISMATCH",
    "ERR_CERT_AUTHORITY_INVALID",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_NETWORK_CHANGED",
    "ERR_SOCKET_NOT_CONNECTED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_CONNECTION_ABORTED",
    "ERR_FAILED",
)

COMPLETED_VERDICTS_FOR_RESUME = {
    "authenticated_verified",
    "authenticated_page",
    "denied_403",
    "login_required",
    "direct_blocked_handoff_2b",
}

SSO_LOGIN_HOSTS = {
    "login.ke.com",
    "login.lianjia.com",
    "auth.lianjia.com",
    "cas.ke.com",
    "sso.ke.com",
}


class CircuitBreakerTriggered(RuntimeError):
    """Raised when 3 consecutive baseline sites fall back to a login page."""


class ManagedSocksTunnel:
    """Manage a temporary SSH dynamic SOCKS5 tunnel via try...finally."""

    def __init__(self, host_alias: str = DEFAULT_SSH_HOST) -> None:
        self.host_alias = host_alias
        self.port: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=32)
        self._stderr_thread: threading.Thread | None = None
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

    def is_healthy(self, timeout: float = 2.0) -> bool:
        """Verify the local SOCKS5 listener responds to a SOCKS5 greeting."""
        if self.proc is None or self.proc.poll() is not None or self.port is None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(b"\x05\x01\x00")
                reply = sock.recv(2)
                return len(reply) == 2 and reply[0] == 5
        except OSError:
            return False

    def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        try:
            while True:
                chunk = self.proc.stderr.readline()
                if not chunk:
                    break
                self._stderr_tail.append(chunk.decode("utf-8", "replace").strip())
        except OSError:
            pass

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
            "-o",
            "LogLevel=ERROR",
            self.host_alias,
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err_msg = " | ".join(self._stderr_tail)[-180:]
                raise RuntimeError(
                    f"SSH tunnel exited early (code={self.proc.returncode}): {err_msg}"
                )
            if self._is_port_listening(self.port) and self.is_healthy(timeout=1.0):
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
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)

        if self.port is not None:
            deadline = time.monotonic() + 3.0
            released = False
            while time.monotonic() < deadline:
                if not self._is_port_listening(self.port, timeout=0.15):
                    released = True
                    break
                time.sleep(0.15)
            self.cleanup_status["port_released"] = released


def sanitize_text(text: str, sensitive_values: set[str], max_len: int = 240) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", str(text)).strip()
    for val in sorted(sensitive_values, key=len, reverse=True):
        if val and val in s:
            s = s.replace(val, "[REDACTED]")
    s = re.sub(r"\b(?:TGT|TGC?|ST)-[A-Za-z0-9._-]{6,}\b", "[REDACTED_TICKET]", s)
    s = re.sub(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{4,})?\b",
        "[REDACTED_JWT]",
        s,
    )
    s = re.sub(
        r"(?i)\bBearer\s+(?!\[REDACTED\b|<REDACTED)[A-Za-z0-9._\-+/=]{12,}",
        "Bearer [REDACTED]",
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
            or bool(re.search(r"^(?:TGT|TGC?|ST)-|^eyJ[A-Za-z0-9_-]{8,}\.", v_str))
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
    if u_str.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "about:", "blob:")):
        return "", ""
    if base_url:
        u_str = urllib.parse.urljoin(base_url, u_str)
    parsed = urllib.parse.urlsplit(u_str)
    if parsed.scheme.lower() not in ("http", "https"):
        return "", ""
    host = (parsed.hostname or "").lower()
    clean_path = sanitize_text(parsed.path or "/", sensitive_values, max_len=300)
    clean_path = re.sub(
        r"(?i)(;(?:jsessionid|sessionid|sid|token)=)[^;/?#]+",
        r"\1[REDACTED]",
        clean_path,
    )
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


def recursive_scrub(obj: Any, sensitive_values: set[str]) -> Any:
    if isinstance(obj, str):
        return sanitize_text(obj, sensitive_values, max_len=1000)
    if isinstance(obj, list):
        return [recursive_scrub(item, sensitive_values) for item in obj]
    if isinstance(obj, dict):
        return {k: recursive_scrub(v, sensitive_values) for k, v in obj.items()}
    return obj


def load_credentials_in_memory(
    ssh_host: str = DEFAULT_SSH_HOST, remote_path: str = DEFAULT_REMOTE_CRED_PATH
) -> tuple[dict[str, Any], dict[str, Any], set[str], set[str], str]:
    """Read remote credential store over short-lived SSH strictly into memory."""
    raw = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            ssh_host,
            "cat",
            remote_path,
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
            business.get("ucid"),
            business.get("userCode"),
        )
        if v and len(str(v).strip()) > 5
    }

    sensitive_values: set[str] = set(identities)
    for d in (store, business, refresh):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ("credential_material_b64", "refresh_material_b64"):
                    continue
                if isinstance(v, str) and len(v.strip()) >= 6:
                    sensitive_values.add(v.strip())
                elif isinstance(v, dict):
                    for sub_v in v.values():
                        if isinstance(sub_v, str) and len(sub_v.strip()) >= 6:
                            sensitive_values.add(sub_v.strip())

    # Resolve CRM puzu lease token for Wandou without printing or persisting
    puzu_token = ""
    for candidate_dict in (business, refresh, store):
        if not isinstance(candidate_dict, dict):
            continue
        for k in ("puzu_lease_token", "puzu_token", "lease_token", "wandou_token"):
            val = candidate_dict.get(k)
            if isinstance(val, str) and val.strip():
                puzu_token = val.strip()
                break
        if puzu_token:
            break
    if not puzu_token:
        for candidate_dict in (business, refresh):
            if not isinstance(candidate_dict, dict):
                continue
            for k, val in candidate_dict.items():
                if "puzu" in str(k).lower() and isinstance(val, str) and val.strip():
                    puzu_token = val.strip()
                    break
            if puzu_token:
                break

    del store
    return business, refresh, identities, sensitive_values, puzu_token


def parent_domains_for_host(host: str) -> list[str]:
    base = [".link.lianjia.com", ".ke.com", ".lianjia.com"]
    hl = (host or "").lower()
    if hl.endswith(".deyoulife.com") and ".deyoulife.com" not in base:
        base.append(".deyoulife.com")
    if hl.endswith(".homelink.com.cn") and ".homelink.com.cn" not in base:
        base.append(".homelink.com.cn")
    return base


def seed_cas_and_business_cookies(
    context: BrowserContext,
    business: dict[str, Any],
    refresh: dict[str, Any],
    target_host: str,
    include_business: bool = True,
) -> None:
    cookies: list[dict[str, Any]] = []
    if include_business:
        domains = parent_domains_for_host(target_host)
        for name, val in business.items():
            if isinstance(val, str) and val:
                for domain in domains:
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
    if cookies:
        context.add_cookies(cookies)


def harvest_sensitive_and_check_identity(
    obj: Any,
    identities: set[str],
    sensitive_values: set[str],
    pii_ui_tokens: set[str],
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
            keys_lower
            & {
                "ucid",
                "usercode",
                "mobile",
                "phone",
                "homephone",
                "orgcode",
                "positioncode",
                "postion",
                "employeeno",
                "email",
            }
        )
        in_user_ctx = (parent_key in USER_CONTEXT_KEYS) or (has_user_markers and not is_nav_like)

        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                vs = str(v).strip()
                if vs in identities:
                    matched = True
                if kl in ALWAYS_SENSITIVE_FIELD_KEYS and vs:
                    if len(vs) >= 2 and not re.match(r"^[0-9]{1,5}$", vs):
                        pii_ui_tokens.add(vs)
                    if len(vs) >= 4 and not re.match(r"^[0-9]{1,5}$", vs):
                        sensitive_values.add(vs)
                elif in_user_ctx and kl in PERSONAL_CONTEXT_NAME_KEYS and vs:
                    if len(vs) >= 2 and not re.match(r"^[0-9]{1,5}$", vs):
                        pii_ui_tokens.add(vs)
                        sensitive_values.add(vs)
                elif (
                    kl in {"token", "access_token", "refresh_token", "credential", "ticket", "password"}
                    and len(vs) >= 8
                ):
                    sensitive_values.add(vs)
            if harvest_sensitive_and_check_identity(
                v, identities, sensitive_values, pii_ui_tokens, kl
            ):
                matched = True
    elif isinstance(obj, list):
        for item in obj:
            if harvest_sensitive_and_check_identity(
                item, identities, sensitive_values, pii_ui_tokens, parent_key
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


UI_MODULES_EXTRACT_JS = r"""
() => {
  const topNav = [];
  const sideNav = [];
  const headings = [];
  const seenTop = new Set();
  const seenSide = new Set();
  const seenHead = new Set();
  let shadowCount = 0;
  let shadowText = '';

  function clean(s) {
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0 || el.offsetParent !== null;
  }

  function addUnique(arr, seen, txt, maxLen, maxItems) {
    if (arr.length >= maxItems) return;
    const t = clean(txt);
    if (!t || t.length < 2 || t.length > maxLen) return;
    if (!seen.has(t)) {
      seen.add(t);
      arr.push(t);
    }
  }

  function isTopContainer(el) {
    return !!el.closest(
      'header, [class*="header"], [class*="topbar"], [class*="top-nav"], [class*="navbar"], .ant-layout-header'
    );
  }

  function isSideOrTabContainer(el) {
    return !!el.closest(
      'aside, [class*="sidebar"], [class*="side-nav"], [class*="sider"], [class*="left-menu"], [role="tablist"], [class*="tabs"], [class*="nav-menu"], .ant-layout-sider, .ant-menu, .el-menu'
    );
  }

  function extractFromRoot(root) {
    // Top navigation items
    const topSelectors = [
      'header a, header button, header [role="menuitem"], header [role="tab"]',
      '[class*="header"] a, [class*="header"] [role="menuitem"], [class*="header"] [class*="nav-item"]',
      '[class*="topbar"] a, [class*="topbar"] [role="menuitem"], [class*="top-nav"] a, [class*="navbar"] a',
      'nav a, nav [role="menuitem"]'
    ];
    for (const sel of topSelectors) {
      for (const el of root.querySelectorAll(sel)) {
        if (isSideOrTabContainer(el) && !isTopContainer(el)) continue;
        if (!isVisible(el)) continue;
        addUnique(topNav, seenTop, el.innerText || el.textContent, 45, 40);
      }
    }

    // Side navigation / tabs / menus
    const sideSelectors = [
      'aside a, aside li, aside [role="menuitem"], aside [class*="menu-item"], aside [class*="title"]',
      '[class*="sidebar"] a, [class*="sidebar"] [class*="menu-item"], [class*="side-nav"] a',
      '[class*="sider"] a, [class*="sider"] [class*="menu-item"], .ant-menu-item, .ant-menu-submenu-title, .el-menu-item, .el-submenu__title',
      '[role="tablist"] [role="tab"], .ant-tabs-tab, .el-tabs__item, [class*="tab-item"]'
    ];
    for (const sel of sideSelectors) {
      for (const el of root.querySelectorAll(sel)) {
        if (!isVisible(el)) continue;
        const t = clean(el.innerText || el.textContent);
        if (seenTop.has(t)) continue;
        addUnique(sideNav, seenSide, t, 45, 50);
      }
    }

    // Main headings, panel titles, function cards
    const headSelectors = [
      'h1, h2, h3, h4',
      '[class*="card"] [class*="title"], [class*="card"] [class*="name"], .ant-card-head-title, .el-card__header',
      '[class*="module"] [class*="title"], [class*="section-title"], [class*="page-title"], [class*="app-name"], .url-card strong'
    ];
    for (const sel of headSelectors) {
      for (const el of root.querySelectorAll(sel)) {
        if (!isVisible(el)) continue;
        addUnique(headings, seenHead, el.innerText || el.textContent, 50, 40);
      }
    }

    // Recurse into Shadow DOM roots
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) {
        shadowCount += 1;
        shadowText += ' ' + clean(el.shadowRoot.textContent || '').slice(0, 600);
        extractFromRoot(el.shadowRoot);
      }
    }
  }

  extractFromRoot(document);

  const passwordInputs = document.querySelectorAll('input[type="password"]').length;
  const bodyText = clean((document.body ? document.body.innerText : '') + ' ' + shadowText).slice(0, 4000);
  const chatLinks = Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.getAttribute('href') || '')
    .filter(h => /chat|im|message|conversation/i.test(h))
    .slice(0, 10);

  return {
    top_nav: topNav,
    side_nav_or_tabs: sideNav,
    page_headings_or_cards: headings,
    shadow_dom_detected: shadowCount > 0,
    shadow_root_count: shadowCount,
    password_inputs: passwordInputs,
    body_text: bodyText,
    chat_links: chatLinks,
    html_len: document.documentElement ? document.documentElement.outerHTML.length : 0,
  };
}
"""


def clean_ui_items(
    items: list[str],
    identities: set[str],
    pii_ui_tokens: set[str],
    sensitive_values: set[str],
    limit: int,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    block_tokens = {t.strip() for t in (identities | pii_ui_tokens) if t and len(t.strip()) >= 2}

    for raw in items:
        s = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not s or len(s) < 2 or len(s) > 48:
            continue
        if re.search(r"安全退出|退出登录|退出系统|注销登录", s):
            continue
        if re.search(r"^(?:你好|欢迎|Hi|Hello)[，,\s]", s, re.I):
            continue
        if re.search(r"工号\s*[:：]|手机\s*[:：]|邮箱\s*[:：]|UCID\s*[:：]", s, re.I):
            continue
        if re.search(r"1[3-9]\d{9}|1\d{2}\*{3,6}\d{3,4}", s):
            continue
        if re.search(r"\b\d{8,}\b", s):
            continue
        if any(tok in s for tok in block_tokens):
            continue
        san = sanitize_text(s, sensitive_values, max_len=48)
        if "[REDACTED" in san:
            continue
        if re.fullmatch(r"[\W_0-9]+", san):
            continue
        if san not in seen:
            seen.add(san)
            cleaned.append(san)
        if len(cleaned) >= limit:
            break
    return cleaned


def is_direct_blocked_exception(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, PlaywrightTimeoutError):
        return True
    return any(marker in msg for marker in DIRECT_BLOCKED_ERR_MARKERS)


def is_login_page(
    final_url: str, final_host: str, title: str, body_text: str, password_inputs: int
) -> bool:
    parsed = urllib.parse.urlsplit(final_url)
    path = parsed.path.lower()
    frag = parsed.fragment.lower()
    if final_host in SSO_LOGIN_HOSTS:
        return True
    if re.search(r"/(?:sso-)?(?:login|signin|cas/login)(?:/|$)", path, re.I):
        return True
    if re.search(r"^/?(?:login|signin)(?:/|\?|$)", frag, re.I):
        return True
    if password_inputs > 0:
        return True
    if re.search(r"^(?:.*统一)?(?:身份)?(?:认证|登录|登陆)|统一认证|用户登录|账号登录|sign\s*in|log\s*in", title.strip(), re.I):
        return True
    if len(body_text) < 400 and re.search(r"请先登录|扫码登录|账号密码登录|统一身份认证", body_text):
        return True
    return False


def is_denied_page(
    http_status: int | None, final_url: str, final_host: str, title: str, body_text: str
) -> bool:
    if http_status in (401, 403):
        return True
    if final_host in {"hip.lianjia.com", "hip.ke.com"}:
        return True
    lower_u = final_url.lower()
    if any(seg in lower_u for seg in ("/forbidden", "/no-permission", "/nopermission", "/no-access", "/noaccess", "/403", "/unauthorized")):
        return True
    if re.search(r"\b403\b|forbidden|无权限|暂无权限|没有访问权限|未授权访问", title, re.I):
        return True
    if len(body_text) < 500 and re.search(r"无权访问|暂无访问权限|您没有权限|403\s+Forbidden|Access\s+Denied", body_text, re.I):
        return True
    return False


def infer_sso_type(
    auth_mode: str,
    verdict: str,
    redirect_hosts: list[str],
    cas_callback_paths: list[str],
    backend_apis: list[dict[str, Any]],
    issued_cookies: list[str],
) -> str:
    if verdict in ("direct_blocked_handoff_2b", "error"):
        return "unknown"
    api_hosts_paths = " ".join(
        f"{ep.get('host', '')}{ep.get('path', '')}" for ep in backend_apis
    ).lower()
    has_sso_redirect = any(h in SSO_LOGIN_HOSTS for h in redirect_hosts) or any(
        "login." in h or "cas" in p.lower() for h in redirect_hosts for p in cas_callback_paths
    )
    has_supabase_or_falcon = any(
        kw in api_hosts_paths for kw in ("supabase", "falcon", "/rest/v1/", "/auth/v1/", "prd-assistant-backend")
    )
    if auth_mode == "wandou_localstorage_token":
        return "localstorage_bearer"
    if auth_mode == "moma_supabase_cas" or (has_supabase_or_falcon and (has_sso_redirect or cas_callback_paths)):
        return "supabase_cas"
    if verdict == "login_required" and not has_sso_redirect:
        return "independent_login"
    if has_sso_redirect or cas_callback_paths:
        return "cas_redirect"
    if any(
        kw in api_hosts_paths
        for kw in ("checkuserlogin", "/cas", "/sso", "login.ke.com", "login.lianjia.com")
    ):
        return "cas_spa"
    if verdict in ("authenticated_verified", "authenticated_page"):
        if any(c in {"SESSION", "JSESSIONID", "PHPSESSID", "rememberMe", "saas_token"} for c in issued_cookies):
            return "cas_redirect"
        return "none"
    return "unknown"


def profile_target_in_context(
    context: BrowserContext,
    target: dict[str, Any],
    network_mode: str,
    timeout_ms: int,
    identities: set[str],
    sensitive_values: set[str],
    business_cookie_names: set[str],
    puzu_token: str,
) -> dict[str, Any]:
    t0 = time.monotonic()
    host = target["host"].lower()
    entry_url = target["url"]
    auth_hint = (target.get("auth_hint") or "cas_cookie").lower()
    if "wandou" in host:
        auth_mode = "wandou_localstorage_token"
    elif auth_hint == "moma_supabase_cas" or host in {"moma.ke.com", "app-moma.ke.com"}:
        auth_mode = "moma_supabase_cas"
    else:
        auth_mode = "cas_cookie"

    entry_parsed = urllib.parse.urlsplit(entry_url)
    target_origin = f"{entry_parsed.scheme}://{entry_parsed.netloc}"

    if auth_mode == "wandou_localstorage_token" and puzu_token:
        # Inject localStorage.token strictly when location.origin matches target_origin
        init_js = (
            f"(() => {{ if (window.location.origin === {json.dumps(target_origin)}) {{ "
            f"try {{ window.localStorage.setItem('token', {json.dumps(puzu_token)}); }} catch (e) {{}} "
            f"}} }})();"
        )
        context.add_init_script(init_js)

    blocked_logout_count = 0
    identity_matched = False
    pii_ui_tokens: set[str] = set()
    issued_cookie_names: set[str] = set()
    redirect_hosts: list[str] = []
    cas_callback_paths: list[str] = []
    backend_apis_map: dict[tuple[str, str, str, int, str], dict[str, Any]] = {}
    errors: list[str] = []
    failed_nav_errors: list[str] = []
    last_main_doc_status: int | None = None

    def add_redirect_host(h: str) -> None:
        hl = (h or "").lower()
        if hl and hl not in redirect_hosts:
            redirect_hosts.append(hl)

    def add_cas_path(p: str) -> None:
        ps = sanitize_text(p or "/", sensitive_values, max_len=120)
        if ps and ps not in cas_callback_paths:
            cas_callback_paths.append(ps)

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

    def on_request(req: Request) -> None:
        try:
            if req.is_navigation_request() and req.frame == page.main_frame:
                parsed = urllib.parse.urlsplit(req.url)
                rhost = (parsed.hostname or "").lower()
                add_redirect_host(rhost)
                if rhost in SSO_LOGIN_HOSTS or "/cas" in parsed.path.lower() or "ticket=" in parsed.query.lower():
                    add_cas_path(parsed.path or "/")
        except Exception:
            pass

    def on_request_failed(req: Request) -> None:
        try:
            if req.is_navigation_request() and req.frame == page.main_frame:
                failure = req.failure or "request_failed"
                san_u, rhost = sanitize_url(req.url, sensitive_values)
                failed_nav_errors.append(
                    sanitize_text(f"{rhost or 'unknown'}: {failure} ({san_u})", sensitive_values, max_len=200)
                )
        except Exception:
            pass

    def on_response(resp: Response) -> None:
        nonlocal identity_matched, last_main_doc_status
        try:
            for hdr in resp.headers_array():
                if hdr.get("name", "").lower() == "set-cookie":
                    cname = hdr.get("value", "").split("=", 1)[0].strip()
                    if cname:
                        issued_cookie_names.add(cname)

            req = resp.request
            r_url = resp.url
            parsed = urllib.parse.urlsplit(r_url)
            rhost = (parsed.hostname or "").lower()
            path = parsed.path or "/"
            res_type = req.resource_type

            if req.is_navigation_request() and req.frame == page.main_frame:
                add_redirect_host(rhost)
                if rhost in SSO_LOGIN_HOSTS or "/cas" in path.lower() or "ticket=" in parsed.query.lower():
                    add_cas_path(path)
                last_main_doc_status = resp.status

            if res_type in ("stylesheet", "image", "font", "media", "other"):
                return
            if STATIC_ASSET_EXT_RE.search(r_url):
                return
            if rhost in TELEMETRY_OR_STATIC_HOSTS:
                return

            ctype = (resp.headers.get("content-type") or "").lower()
            biz_code: Any = None
            is_api_like = res_type in ("xhr", "fetch") or "json" in ctype

            if is_api_like and ("json" in ctype or "text" in ctype or not ctype):
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
                                data, identities, sensitive_values, pii_ui_tokens
                            ):
                                identity_matched = True
                            biz_code = extract_biz_code(data)
                        except json.JSONDecodeError:
                            pass

            if is_api_like or (res_type == "document" and ("/cas" in path.lower() or "/auth" in path.lower() or rhost in SSO_LOGIN_HOSTS)):
                clean_path = sanitize_text(path, sensitive_values, max_len=240)
                ep_key = (req.method, rhost, clean_path, resp.status, str(biz_code))
                backend_apis_map[ep_key] = {
                    "method": req.method,
                    "host": rhost,
                    "path": clean_path,
                    "status": resp.status,
                    "biz_code": biz_code,
                }
        except Exception:
            pass

    page.on("request", on_request)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)

    http_status: int | None = None
    final_url_raw = entry_url
    title_raw = ""
    dom_info: dict[str, Any] = {
        "top_nav": [],
        "side_nav_or_tabs": [],
        "page_headings_or_cards": [],
        "shadow_dom_detected": False,
        "password_inputs": 0,
        "body_text": "",
        "chat_links": [],
        "html_len": 0,
    }
    direct_blocked = False

    try:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        try:
            if auth_mode == "wandou_localstorage_token":
                # 1) First access origin and ensure localStorage.token is set on that origin only
                rem_ms = max(3000, int((deadline - time.monotonic()) * 1000))
                resp = page.goto(
                    target_origin + "/",
                    wait_until="domcontentloaded",
                    timeout=min(timeout_ms, rem_ms),
                )
                if resp is not None:
                    http_status = resp.status
                cur_parsed = urllib.parse.urlsplit(page.url)
                if f"{cur_parsed.scheme}://{cur_parsed.netloc}" == target_origin and puzu_token:
                    try:
                        page.evaluate(
                            "(t) => { window.localStorage.setItem('token', t); }",
                            puzu_token,
                        )
                    except Exception:
                        pass
                try:
                    rem_ms = max(500, int((deadline - time.monotonic()) * 1000))
                    page.wait_for_load_state("networkidle", timeout=min(6000, rem_ms))
                except PlaywrightTimeoutError:
                    pass
                rem_ms = int((deadline - time.monotonic()) * 1000)
                if rem_ms > 500:
                    page.wait_for_timeout(min(2000, rem_ms))

                # 2) If entry_url differs or /api/v1/users/me not seen and a chat link exists, navigate to chat entry
                seen_users_me = any(
                    "/api/v1/users/me" in ep["path"] for ep in backend_apis_map.values()
                )
                try:
                    pre_dom = page.evaluate(UI_MODULES_EXTRACT_JS)
                except Exception:
                    pre_dom = {}
                chat_target = ""
                if entry_url.rstrip("/") != target_origin.rstrip("/"):
                    chat_target = entry_url
                elif not seen_users_me and pre_dom.get("chat_links"):
                    chat_target = urllib.parse.urljoin(target_origin + "/", pre_dom["chat_links"][0])
                elif not seen_users_me and (
                    cur_parsed.hostname in SSO_LOGIN_HOSTS
                    or "login" in page.url.lower()
                    or int(pre_dom.get("password_inputs") or 0) > 0
                ):
                    chat_target = target_origin + "/chat"

                if chat_target and int((deadline - time.monotonic()) * 1000) > 3500:
                    rem_ms = max(2500, int((deadline - time.monotonic()) * 1000))
                    resp2 = page.goto(
                        chat_target,
                        wait_until="domcontentloaded",
                        timeout=rem_ms,
                    )
                    if resp2 is not None:
                        http_status = resp2.status
                    try:
                        rem_ms = max(500, int((deadline - time.monotonic()) * 1000))
                        page.wait_for_load_state("networkidle", timeout=min(6000, rem_ms))
                    except PlaywrightTimeoutError:
                        pass
                    rem_ms = int((deadline - time.monotonic()) * 1000)
                    if rem_ms > 400:
                        page.wait_for_timeout(min(2000, rem_ms))
            else:
                rem_ms = max(3000, int((deadline - time.monotonic()) * 1000))
                resp = page.goto(
                    entry_url,
                    wait_until="domcontentloaded",
                    timeout=min(timeout_ms, rem_ms),
                )
                if resp is not None:
                    http_status = resp.status
                try:
                    rem_ms = max(500, int((deadline - time.monotonic()) * 1000))
                    page.wait_for_load_state("networkidle", timeout=min(6000, rem_ms))
                except PlaywrightTimeoutError:
                    pass
                rem_ms = int((deadline - time.monotonic()) * 1000)
                if rem_ms > 400:
                    page.wait_for_timeout(min(2000, rem_ms))
                # If an SPA triggered a late CAS redirect or ticket callback, wait briefly for settle
                cur_u = page.url or ""
                if ("ticket=" in cur_u.lower() or any(h in cur_u.lower() for h in SSO_LOGIN_HOSTS)) and int((deadline - time.monotonic()) * 1000) > 2000:
                    try:
                        rem_ms = max(500, int((deadline - time.monotonic()) * 1000))
                        page.wait_for_load_state("networkidle", timeout=min(4000, rem_ms))
                    except PlaywrightTimeoutError:
                        pass
                    rem_ms = int((deadline - time.monotonic()) * 1000)
                    if rem_ms > 300:
                        page.wait_for_timeout(min(1500, rem_ms))
        except Exception as exc:
            err_msg = sanitize_text(
                f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}",
                sensitive_values,
            )
            errors.append(err_msg)
            if network_mode == "direct_local":
                if is_direct_blocked_exception(exc) and last_main_doc_status is None:
                    direct_blocked = True
                elif is_direct_blocked_exception(exc) and any(
                    m in err_msg for m in DIRECT_BLOCKED_ERR_MARKERS
                ):
                    direct_blocked = True

        # Combine final frame status & DOM safely
        if last_main_doc_status is not None:
            http_status = last_main_doc_status
        try:
            if page.url and page.url != "about:blank":
                final_url_raw = page.url
                title_raw = page.title() or ""
        except Exception:
            pass

        if not direct_blocked and page.url and page.url != "about:blank":
            merged_top: list[str] = []
            merged_side: list[str] = []
            merged_head: list[str] = []
            shadow_detected = False
            total_pw = 0
            body_chunks: list[str] = []
            for frame in page.frames:
                try:
                    fd = frame.evaluate(UI_MODULES_EXTRACT_JS)
                except Exception:
                    continue
                merged_top.extend(fd.get("top_nav") or [])
                merged_side.extend(fd.get("side_nav_or_tabs") or [])
                merged_head.extend(fd.get("page_headings_or_cards") or [])
                if fd.get("shadow_dom_detected"):
                    shadow_detected = True
                total_pw += int(fd.get("password_inputs") or 0)
                bt = fd.get("body_text") or ""
                if bt:
                    if any(ident in bt for ident in identities):
                        identity_matched = True
                    body_chunks.append(bt[:2000])
            dom_info = {
                "top_nav": merged_top,
                "side_nav_or_tabs": merged_side,
                "page_headings_or_cards": merged_head,
                "shadow_dom_detected": shadow_detected,
                "password_inputs": total_pw,
                "body_text": " ".join(body_chunks),
            }

        for ferr in failed_nav_errors:
            if ferr not in errors:
                errors.append(ferr)
            if any(m in ferr for m in DIRECT_BLOCKED_ERR_MARKERS):
                # If a main navigation redirect failed on DNS/connection reset in direct mode
                if network_mode == "direct_local" and (
                    http_status is None or final_url_raw == "about:blank" or host in ferr
                ):
                    direct_blocked = True

        sanitized_entry_url, _ = sanitize_url(entry_url, sensitive_values)
        sanitized_final_url, final_host = sanitize_url(final_url_raw, sensitive_values)
        if not sanitized_final_url:
            sanitized_final_url = sanitized_entry_url
            final_host = host
        sanitized_title = sanitize_text(title_raw, sensitive_values, max_len=120)

        # Determine access_verdict
        has_redirect_loop = any("ERR_TOO_MANY_REDIRECTS" in e for e in errors)
        if direct_blocked or (http_status is None and network_mode == "direct_local" and errors):
            verdict = "direct_blocked_handoff_2b"
        elif has_redirect_loop:
            verdict = (
                "login_required"
                if any(h in SSO_LOGIN_HOSTS for h in redirect_hosts)
                else "error"
            )
        elif http_status is None and errors:
            verdict = "error"
        elif is_login_page(
            sanitized_final_url,
            final_host,
            sanitized_title,
            dom_info.get("body_text", ""),
            int(dom_info.get("password_inputs") or 0),
        ):
            verdict = "login_required"
        elif is_denied_page(
            http_status,
            sanitized_final_url,
            final_host,
            sanitized_title,
            dom_info.get("body_text", ""),
        ):
            verdict = "denied_403"
        elif http_status is not None and http_status >= 500:
            verdict = "error"
            if not errors:
                errors.append(f"HTTP_{http_status}")
        elif http_status is not None and 200 <= http_status < 400:
            verdict = "authenticated_verified" if identity_matched else "authenticated_page"
        elif http_status == 404 and not (
            dom_info["top_nav"] or dom_info["side_nav_or_tabs"] or dom_info["page_headings_or_cards"]
        ):
            verdict = "error"
            if not errors:
                errors.append("HTTP_404")
        else:
            verdict = "error"

        # Issued cookies (names only, excluding pre-seeded business cookies unless freshly Set-Cookie'd)
        cookie_names = set(issued_cookie_names)
        for c in context.cookies():
            cname = c.get("name", "")
            cdom = (c.get("domain") or "").lstrip(".").lower()
            if not cname:
                continue
            if cdom and (
                host == cdom or host.endswith(f".{cdom}") or cdom.endswith(host)
            ):
                if cname not in business_cookie_names:
                    cookie_names.add(cname)

        backend_apis = sorted(
            backend_apis_map.values(),
            key=lambda x: (x["host"], x["path"], x["method"]),
        )
        backend_api_hosts = sorted(
            {
                ep["host"]
                for ep in backend_apis
                if ep.get("host") and ep["host"] not in TELEMETRY_OR_STATIC_HOSTS
            }
        )

        sso_type = infer_sso_type(
            auth_mode,
            verdict,
            redirect_hosts,
            cas_callback_paths,
            backend_apis,
            sorted(cookie_names),
        )

        ui_modules = {
            "top_nav": clean_ui_items(
                dom_info.get("top_nav") or [],
                identities,
                pii_ui_tokens,
                sensitive_values,
                limit=25,
            ),
            "side_nav_or_tabs": clean_ui_items(
                dom_info.get("side_nav_or_tabs") or [],
                identities,
                pii_ui_tokens,
                sensitive_values,
                limit=30,
            ),
            "page_headings_or_cards": clean_ui_items(
                dom_info.get("page_headings_or_cards") or [],
                identities,
                pii_ui_tokens,
                sensitive_values,
                limit=25,
            ),
            "shadow_dom_detected": bool(dom_info.get("shadow_dom_detected")),
        }

        record: dict[str, Any] = {
            "host": host,
            "name": target.get("name", host),
            "category": target.get("category", ""),
            "batch": target.get("batch", ""),
            "network_mode": network_mode,
            "entry_url": sanitized_entry_url,
            "final_url": sanitized_final_url,
            "redirect_hosts": redirect_hosts,
            "http_status": http_status,
            "title": sanitized_title,
            "access_verdict": verdict,
            "identity_match": bool(identity_matched),
            "auth_mechanism": {
                "sso_type": sso_type,
                "cas_callback_paths": cas_callback_paths,
                "issued_cookie_names": sorted(cookie_names),
            },
            "ui_modules": ui_modules,
            "backend_apis": backend_apis,
            "backend_api_hosts": backend_api_hosts,
            "errors": errors,
            "elapsed_seconds": round(time.monotonic() - t0, 2),
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if blocked_logout_count > 0:
            record["blocked_logout_requests"] = blocked_logout_count

        return recursive_scrub(record, sensitive_values)
    finally:
        try:
            page.close()
        except Exception:
            pass


def load_targets(targets_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(targets_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("targets"), list):
        return raw["targets"]
    raise ValueError(f"Unsupported targets JSON format in {targets_path}")


def load_resume_records(output_jsonl: Path) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Return deduplicated completed records and set of completed hostnames."""
    kept: dict[str, dict[str, Any]] = {}
    if not output_jsonl.exists():
        return kept, set()
    for line in output_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        try:
            rec = json.loads(line_s)
        except json.JSONDecodeError:
            continue
        host = (rec.get("host") or "").lower()
        verdict = rec.get("access_verdict")
        if host and verdict in COMPLETED_VERDICTS_FOR_RESUME:
            kept[host] = rec
    return kept, set(kept.keys())


def profile_all_targets(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets_json)
    output_jsonl: Path = args.output_jsonl

    kept_records: dict[str, dict[str, Any]] = {}
    completed_hosts: set[str] = set()
    if args.resume and output_jsonl.exists():
        kept_records, completed_hosts = load_resume_records(output_jsonl)
        # Compact output_jsonl with only valid completed records before appending
        with output_jsonl.open("w", encoding="utf-8") as f:
            for rec in kept_records.values():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
    else:
        output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        output_jsonl.write_text("", encoding="utf-8")

    pending_targets = [
        t for t in targets if (t.get("host") or "").lower() not in completed_hosts
    ]
    if not pending_targets:
        print(
            json.dumps(
                {
                    "status": "already_complete",
                    "total_targets": len(targets),
                    "resumed_skipped": len(completed_hosts),
                },
                ensure_ascii=False,
            )
        )
        return 0

    business, refresh, identities, sensitive_values, puzu_token = load_credentials_in_memory(
        ssh_host=args.ssh_host, remote_path=args.remote_cred_path
    )
    print(
        f"Loaded in-memory session material (business_cookies={len(business)}, "
        f"has_tgc={bool(refresh.get('TGC'))}, has_puzu_token={bool(puzu_token)})",
        flush=True,
    )
    business_cookie_names = set(business.keys())
    network_mode = "socks5_tunnel" if args.use_tunnel else "direct_local"
    consecutive_baseline_login = 0
    tunnel_cleanup_status: dict[str, Any] | None = None
    circuit_broken = False
    newly_written: list[dict[str, Any]] = []

    consecutive_tunnel_errors = 0
    tunnel_restarts = 0
    max_tunnel_restarts = 5
    pending_idx = 0

    def probe_single_target(browser: Browser, target: dict[str, Any], include_business: bool) -> dict[str, Any]:
        host = (target.get("host") or "").lower()
        auth_hint = (target.get("auth_hint") or "cas_cookie").lower()
        ctx = browser.new_context(ignore_https_errors=False)
        try:
            if auth_hint == "wandou_localstorage_token" or "wandou" in host:
                pass
            elif (
                not include_business
                or auth_hint == "moma_supabase_cas"
                or host in {"moma.ke.com", "app-moma.ke.com"}
            ):
                seed_cas_and_business_cookies(
                    ctx, business, refresh, host, include_business=False
                )
            else:
                seed_cas_and_business_cookies(
                    ctx, business, refresh, host, include_business=True
                )

            return profile_target_in_context(
                context=ctx,
                target=target,
                network_mode=network_mode,
                timeout_ms=args.timeout_ms,
                identities=identities,
                sensitive_values=sensitive_values,
                business_cookie_names=business_cookie_names,
                puzu_token=puzu_token,
            )
        finally:
            ctx.close()

    def run_with_browser(
        browser: Browser, active_tunnel: ManagedSocksTunnel | None = None
    ) -> bool:
        nonlocal consecutive_baseline_login, circuit_broken, pending_idx, consecutive_tunnel_errors
        with output_jsonl.open("a", encoding="utf-8") as out_f:
            while pending_idx < len(pending_targets):
                target = pending_targets[pending_idx]
                host = (target.get("host") or "").lower()
                is_baseline = (
                    host in BASELINE_HOSTS
                    or target.get("category") == "baseline_public_direct"
                )
                auth_hint = (target.get("auth_hint") or "cas_cookie").lower()

                record = probe_single_target(browser, target, include_business=True)

                # If pre-seeded stale business cookies triggered a login/redirect loop on CAS, retry once with TGC-only
                if (
                    auth_hint == "cas_cookie"
                    and "wandou" not in host
                    and host not in {"moma.ke.com", "app-moma.ke.com"}
                    and (
                        record["access_verdict"] == "login_required"
                        or any("ERR_TOO_MANY_REDIRECTS" in e for e in record.get("errors", []))
                    )
                ):
                    retry_rec = probe_single_target(browser, target, include_business=False)
                    if retry_rec["access_verdict"] in (
                        "authenticated_verified",
                        "authenticated_page",
                        "denied_403",
                    ):
                        record = retry_rec

                verdict = record["access_verdict"]
                if active_tunnel is not None and verdict == "error":
                    consecutive_tunnel_errors += 1
                    tunnel_dead = not active_tunnel.is_healthy(timeout=1.5)
                    if (tunnel_dead or consecutive_tunnel_errors >= 2) and tunnel_restarts < max_tunnel_restarts:
                        print(
                            f"[{pending_idx + 1}/{len(pending_targets)}] {host} -> error "
                            f"(tunnel_healthy={not tunnel_dead}); recycling SOCKS5 tunnel and retrying...",
                            flush=True,
                        )
                        return True
                else:
                    consecutive_tunnel_errors = 0

                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()
                newly_written.append(record)

                print(
                    f"[{pending_idx + 1}/{len(pending_targets)}] {host} -> {verdict} "
                    f"(status={record['http_status']}, id={record['identity_match']}, "
                    f"apis={len(record['backend_apis'])}, {record['elapsed_seconds']}s)",
                    flush=True,
                )
                pending_idx += 1

                if is_baseline:
                    if verdict == "login_required":
                        consecutive_baseline_login += 1
                        if consecutive_baseline_login >= CIRCUIT_BREAKER_THRESHOLD:
                            circuit_broken = True
                            raise CircuitBreakerTriggered(
                                "Circuit breaker triggered: 3 consecutive baseline sites fell back to login page"
                            )
                    elif verdict != "direct_blocked_handoff_2b":
                        consecutive_baseline_login = 0
        return False

    try:
        with sync_playwright() as p:
            if args.use_tunnel:
                while pending_idx < len(pending_targets):
                    with ManagedSocksTunnel(args.ssh_host) as tunnel:
                        browser = p.chromium.launch(
                            headless=True,
                            channel="chrome",
                            proxy={"server": f"socks5://127.0.0.1:{tunnel.port}"},
                            args=[
                                "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1, EXCLUDE localhost"
                            ],
                        )
                        try:
                            need_restart = run_with_browser(browser, active_tunnel=tunnel)
                        finally:
                            browser.close()
                    tunnel_cleanup_status = tunnel.cleanup_status
                    if not need_restart:
                        break
                    tunnel_restarts += 1
                    consecutive_tunnel_errors = 0
            else:
                browser = p.chromium.launch(headless=True, channel="chrome")
                try:
                    run_with_browser(browser)
                finally:
                    browser.close()
    finally:
        business.clear()
        refresh.clear()
        puzu_token = ""
        sensitive_values.clear()

    all_records = list(kept_records.values()) + newly_written
    if args.handoff_json:
        handoff_items = [
            {
                "host": r["host"],
                "name": r["name"],
                "url": r["entry_url"],
                "category": r["category"],
                "from_batch": r["batch"],
                "target_batch": "2B",
                "auth_hint": (
                    "wandou_localstorage_token"
                    if "wandou" in r["host"]
                    else ("moma_supabase_cas" if "moma" in r["host"] else "cas_cookie")
                ),
                "reason": r["errors"][0] if r["errors"] else r["access_verdict"],
                "access_verdict": r["access_verdict"],
                "checked_at_utc": r["checked_at_utc"],
            }
            for r in all_records
            if r["access_verdict"] == "direct_blocked_handoff_2b"
        ]
        args.handoff_json.write_text(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source_jsonl": output_jsonl.name,
                    "handoff_count": len(handoff_items),
                    "targets": handoff_items,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    verdict_counts: dict[str, int] = {}
    for r in all_records:
        v = r["access_verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    print(
        json.dumps(
            {
                "status": "circuit_breaker" if circuit_broken else "ok",
                "network_mode": network_mode,
                "total_records": len(all_records),
                "resumed_skipped": len(completed_hosts),
                "newly_probed": len(newly_written),
                "verdict_counts": verdict_counts,
                "tunnel_cleanup": tunnel_cleanup_status,
            },
            ensure_ascii=False,
        )
    )
    return 2 if circuit_broken else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reusable Playwright Batch Site Profiler for Phase 2A/2B."
    )
    parser.add_argument(
        "--targets-json", type=Path, required=True, help="Input targets JSON path."
    )
    parser.add_argument(
        "--output-jsonl", type=Path, required=True, help="Output JSONL path."
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip hosts already completed without transient errors in output JSONL.",
    )
    parser.add_argument(
        "--use-tunnel",
        action="store_true",
        help="Enable managed temporary SSH SOCKS5 tunnel via try...finally.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="Per-site hard timeout in milliseconds (default: 25000).",
    )
    parser.add_argument(
        "--ssh-host",
        type=str,
        default=DEFAULT_SSH_HOST,
        help="SSH host alias for credential loading and optional SOCKS5 tunnel.",
    )
    parser.add_argument(
        "--remote-cred-path",
        type=str,
        default=DEFAULT_REMOTE_CRED_PATH,
        help="Remote credential store path read strictly into memory.",
    )
    parser.add_argument(
        "--handoff-json",
        type=Path,
        default=None,
        help="Optional output path for direct_blocked_handoff_2b items.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return profile_all_targets(args)


if __name__ == "__main__":
    sys.exit(main())
