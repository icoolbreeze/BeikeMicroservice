#!/usr/bin/env python3
"""In-memory PII & secret review and scrubbing for site_profiles_2b_p1p2.jsonl.

Loads the current employee's name, employee ID / userCode, UCID, phone, email,
department/org names, and session tokens strictly in memory (never printed or
persisted), verifies against all string fields (especially title and ui_modules)
of site_profiles_2b_p1p2.jsonl, redacts or removes any leaked personal items,
and reports only anonymized counts and field/host locations.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase2_site_profiler import (  # noqa: E402
    DEFAULT_REMOTE_CRED_PATH,
    DEFAULT_SSH_HOST,
    LOGOUT_PATH_RE,
    parent_domains_for_host,
    sanitize_text,
)
from redaction_check import RULES as REDACTION_RULES  # noqa: E402

WORKSPACE_ROOT = SCRIPT_DIR.parent
DEFAULT_JSONL_PATH = WORKSPACE_ROOT / "site_profiles_2b_p1p2.jsonl"

IDENTITY_KEY_EXACT = {
    "employee_principal",
    "ucid",
    "lianjia_bucid",
    "usercode",
    "user_code",
    "employeeno",
    "employee_no",
    "employeeid",
    "employee_id",
    "empno",
    "emp_no",
    "realname",
    "real_name",
    "employeename",
    "employee_name",
    "username",
    "user_name",
    "displayname",
    "display_name",
    "truename",
    "true_name",
    "cname",
    "ename",
    "name",
    "nickname",
    "nick_name",
    "phone",
    "mobile",
    "telephone",
    "tel",
    "homephone",
    "compphone",
    "email",
    "mail",
    "idcard",
    "idnumber",
    "bankcard",
    "orgname",
    "org_name",
    "deptname",
    "dept_name",
    "departmentname",
    "department_name",
    "shopname",
    "shop_name",
    "storename",
    "store_name",
    "teamname",
    "team_name",
    "lianjia_busername",
    "lianjia_user_name",
}

GENERIC_NON_PII = {
    "true",
    "false",
    "null",
    "none",
    "admin",
    "user",
    "guest",
    "beike",
    "lianjia",
    "deyou",
    "ke.com",
    "lianjia.com",
    "default",
    "unknown",
    "success",
    "ok",
    "贝壳",
    "链家",
    "德佑",
    "首页",
    "个人中心",
    "管理员",
}

PUBLIC_PROFILE_ENDPOINTS = (
    "https://helper.ke.com/web/loginUser/info",
    "https://luoshu.ke.com/web/loginUser/info",
    "https://m-jury.ke.com/api/jury/getUserInfo",
)


def _add_candidate_token(
    raw_val: Any,
    exact_tokens: set[str],
    min_ascii_len: int = 5,
    min_cjk_len: int = 2,
) -> None:
    if raw_val is None or isinstance(raw_val, bool):
        return
    if isinstance(raw_val, (int, float)):
        s = str(raw_val).strip()
    elif isinstance(raw_val, str):
        s = raw_val.strip()
    else:
        return
    if not s:
        return

    variants = {s}
    if "%" in s:
        try:
            unq = urllib.parse.unquote_plus(s).strip()
            if unq:
                variants.add(unq)
        except Exception:
            pass

    for item in variants:
        if item.lower() in GENERIC_NON_PII:
            continue
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", item))
        if has_cjk:
            if len(item) >= min_cjk_len and len(item) <= 40:
                exact_tokens.add(item)
        else:
            if len(item) >= min_ascii_len and not re.fullmatch(r"\d{1,5}", item):
                exact_tokens.add(item)


def _harvest_profile_obj(obj: Any, exact_tokens: set[str], parent_key: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).strip().lower()
            if kl in IDENTITY_KEY_EXACT:
                _add_candidate_token(v, exact_tokens, min_ascii_len=4, min_cjk_len=2)
            elif any(
                part in kl
                for part in (
                    "ucid",
                    "usercode",
                    "employeeno",
                    "empno",
                    "realname",
                    "employeename",
                    "phone",
                    "mobile",
                    "email",
                    "idcard",
                    "bankcard",
                    "dept",
                    "orgname",
                )
            ):
                _add_candidate_token(v, exact_tokens, min_ascii_len=5, min_cjk_len=2)
            _harvest_profile_obj(v, exact_tokens, kl)
    elif isinstance(obj, list):
        for item in obj:
            _harvest_profile_obj(item, exact_tokens, parent_key)


def _fetch_public_user_profile(
    business: dict[str, Any],
    refresh: dict[str, Any],
    exact_tokens: set[str],
) -> int:
    """Query read-only public-direct CAS user info endpoints in memory."""
    fetched_profiles = 0
    for endpoint in PUBLIC_PROFILE_ENDPOINTS:
        parsed = urllib.parse.urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        jar = httpx.Cookies()
        for domain in parent_domains_for_host(host):
            for name, val in business.items():
                if isinstance(val, str) and val:
                    jar.set(name, val, domain=domain, path="/")
        for name in ("TGC", "TGC_Secure"):
            val = refresh.get(name)
            if isinstance(val, str) and val:
                jar.set(name, val, domain="login.ke.com", path="/")

        try:
            with httpx.Client(
                cookies=jar,
                timeout=8.0,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                cur_url = endpoint
                for _ in range(8):
                    if LOGOUT_PATH_RE.search(cur_url):
                        break
                    resp = client.get(cur_url)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            break
                        nxt = urllib.parse.urljoin(cur_url, loc)
                        nhost = (urllib.parse.urlsplit(nxt).hostname or "").lower()
                        if not nhost.endswith((".ke.com", ".lianjia.com")):
                            break
                        cur_url = nxt
                        continue
                    if resp.status_code == 200:
                        txt = resp.text.lstrip()
                        if txt.startswith("{"):
                            data = json.loads(txt)
                            _harvest_profile_obj(data, exact_tokens)
                            fetched_profiles += 1
                    break
        except Exception:
            continue
    return fetched_profiles


def collect_subject_pii_in_memory(
    ssh_host: str = DEFAULT_SSH_HOST,
    remote_path: str = DEFAULT_REMOTE_CRED_PATH,
) -> tuple[set[str], set[str], dict[str, int]]:
    """Load remote credential store + public profile strictly in memory."""
    raw = subprocess.check_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            ssh_host,
            "cat",
            remote_path,
        ],
        timeout=30,
    )
    store = json.loads(raw.decode("utf-8"))
    del raw

    business = json.loads(base64.b64decode(store["credential_material_b64"]))
    refresh = json.loads(base64.b64decode(store["refresh_material_b64"]))

    exact_tokens: set[str] = set()
    secret_values: set[str] = set()

    for container in (store, business, refresh):
        if isinstance(container, dict):
            for k, v in container.items():
                if k in ("credential_material_b64", "refresh_material_b64"):
                    continue
                if isinstance(v, str) and len(v.strip()) >= 6:
                    secret_values.add(v.strip())
                    if "%" in v:
                        try:
                            unq = urllib.parse.unquote_plus(v).strip()
                            if len(unq) >= 2:
                                _add_candidate_token(unq, exact_tokens, min_ascii_len=5, min_cjk_len=2)
                        except Exception:
                            pass
            _harvest_profile_obj(container, exact_tokens)

    fetched = _fetch_public_user_profile(business, refresh, exact_tokens)

    stats = {
        "exact_pii_token_count": len(exact_tokens),
        "secret_token_count": len(secret_values),
        "public_profiles_harvested": fetched,
    }

    del store
    business.clear()
    refresh.clear()
    return exact_tokens, secret_values, stats


def _find_pii_reasons(
    text: str,
    exact_tokens: set[str],
    secret_values: set[str],
    is_ui_or_title: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if not text:
        return reasons
    lower_t = text.lower()
    for tok in exact_tokens:
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", tok))
        if has_cjk:
            if tok in text:
                reasons.append("subject_cjk_token")
                break
        else:
            if tok.lower() in lower_t:
                reasons.append("subject_ascii_token")
                break
    for sec in secret_values:
        if sec and sec in text:
            reasons.append("secret_token")
            break
    for rule_name, pattern in REDACTION_RULES:
        if pattern.search(text):
            reasons.append(f"rule:{rule_name}")
    if is_ui_or_title:
        if re.search(r"1\d{2}\*{3,6}\d{3,4}", text):
            reasons.append("masked_phone")
        if re.search(r"(?:工号|UCID|手机|邮箱|姓名)\s*[:：]\s*\S+", text, re.I):
            reasons.append("labeled_pii_text")
        if re.search(r"^(?:你好|欢迎|Hi|Hello)[，,\s]+\S+", text, re.I):
            reasons.append("personal_greeting")
    return reasons


def _scrub_string(
    text: str,
    exact_tokens: set[str],
    secret_values: set[str],
    is_ui_or_title: bool = False,
) -> tuple[str, list[str]]:
    reasons = _find_pii_reasons(text, exact_tokens, secret_values, is_ui_or_title=is_ui_or_title)
    if not reasons:
        return text, []

    s = text
    for tok in sorted(exact_tokens, key=len, reverse=True):
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", tok))
        if has_cjk:
            if tok in s:
                s = s.replace(tok, "[REDACTED]")
        else:
            if tok.lower() in s.lower():
                s = re.sub(re.escape(tok), "[REDACTED]", s, flags=re.I)

    s = sanitize_text(s, secret_values, max_len=max(len(s) + 64, 1000))
    if is_ui_or_title:
        s = re.sub(r"1\d{2}\*{3,6}\d{3,4}", "[REDACTED_PHONE]", s)
        s = re.sub(
            r"((?:工号|UCID|手机|邮箱|姓名)\s*[:：]\s*)\S+",
            r"\1[REDACTED]",
            s,
            flags=re.I,
        )
        s = re.sub(
            r"^((?:你好|欢迎|Hi|Hello)[，,\s]+).+$",
            r"\1[REDACTED]",
            s,
            flags=re.I,
        )
    return s, reasons


SENSITIVE_URL_PARAM_RE = re.compile(
    r"([?&#](?:[A-Za-z0-9_.-]*(?:ticket|token|code|secret|auth|session|sign|signature|"
    r"loginname|username|usercode|empno|employeeno|employeeid|ucid|phone|mobile|email|pwd|password)"
    r"[A-Za-z0-9_.-]*)=)([^&#]+)",
    re.I,
)


def scrub_url_fragments_and_queries(url_str: str, extra_tokens: set[str] | None = None) -> tuple[str, bool]:
    """Scrub sensitive query and hash-fragment parameters (e.g. loginName, sign) in URLs."""
    if not url_str:
        return url_str, False
    changed = False

    def _repl(m: re.Match[str]) -> str:
        nonlocal changed
        key_part = m.group(1)
        val_part = m.group(2)
        if val_part.startswith("[REDACTED"):
            return m.group(0)
        if extra_tokens is not None and "loginname" in key_part.lower() and len(val_part) >= 4:
            extra_tokens.add(val_part)
        changed = True
        return f"{key_part}[REDACTED]"

    cleaned = SENSITIVE_URL_PARAM_RE.sub(_repl, url_str)
    return cleaned, changed


def scrub_record(
    rec: dict[str, Any],
    exact_tokens: set[str],
    secret_values: set[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Recursively inspect and scrub all string fields of one JSONL record."""
    host = str(rec.get("host") or "")
    findings: list[dict[str, str]] = []

    def walk(obj: Any, path: str) -> Any:
        if isinstance(obj, str):
            cur = obj
            if path in ("entry_url", "final_url") or cur.startswith(("http://", "https://")):
                cur, url_changed = scrub_url_fragments_and_queries(cur, exact_tokens)
                if url_changed:
                    findings.append(
                        {
                            "host": host,
                            "field": path,
                            "reasons": "sensitive_url_fragment_or_query_param",
                            "action": "redacted",
                        }
                    )
            is_ui_or_title = path == "title" or path.startswith("ui_modules.")
            cleaned, reasons = _scrub_string(
                cur, exact_tokens, secret_values, is_ui_or_title=is_ui_or_title
            )
            if reasons:
                findings.append(
                    {
                        "host": host,
                        "field": path,
                        "reasons": ",".join(sorted(set(reasons))),
                        "action": "redacted",
                    }
                )
            return cleaned
        if isinstance(obj, list):
            out_list: list[Any] = []
            for idx, item in enumerate(obj):
                item_path = f"{path}[{idx}]"
                if path.startswith("ui_modules.") and isinstance(item, str):
                    reasons = _find_pii_reasons(
                        item, exact_tokens, secret_values, is_ui_or_title=True
                    )
                    if reasons:
                        findings.append(
                            {
                                "host": host,
                                "field": item_path,
                                "reasons": ",".join(sorted(set(reasons))),
                                "action": "redacted_or_dropped_ui_item",
                            }
                        )
                        cleaned_item, _ = _scrub_string(
                            item, exact_tokens, secret_values, is_ui_or_title=True
                        )
                        if cleaned_item.strip() in {"[REDACTED]", "[REDACTED_PHONE]", "[REDACTED_EMAIL]"}:
                            continue
                        out_list.append(cleaned_item)
                        continue
                out_list.append(walk(item, item_path))
            return out_list
        if isinstance(obj, dict):
            return {
                k: walk(v, f"{path}.{k}" if path else str(k))
                for k, v in obj.items()
            }
        return obj

    scrubbed = walk(rec, "")
    return scrubbed, findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="In-memory PII review & scrub for site_profiles_2b_p1p2.jsonl."
    )
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL_PATH)
    parser.add_argument("--ssh-host", type=str, default=DEFAULT_SSH_HOST)
    parser.add_argument("--remote-cred-path", type=str, default=DEFAULT_REMOTE_CRED_PATH)
    parser.add_argument(
        "--offline-url-scrub",
        action="store_true",
        help="Run deterministic URL query/fragment & rule-based scrub only (without SSH).",
    )
    args = parser.parse_args()

    jsonl_path = args.jsonl.resolve()
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Missing target JSONL: {jsonl_path}")

    if args.offline_url_scrub:
        exact_tokens: set[str] = set()
        secret_values: set[str] = set()
        token_stats = {
            "exact_pii_token_count": 0,
            "secret_token_count": 0,
            "public_profiles_harvested": 0,
            "mode": "offline_url_scrub",
        }
    else:
        exact_tokens, secret_values, token_stats = collect_subject_pii_in_memory(
            ssh_host=args.ssh_host,
            remote_path=args.remote_cred_path,
        )

    try:
        raw_lines = [
            line.strip()
            for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = [json.loads(line) for line in raw_lines]

        all_findings: list[dict[str, str]] = []
        scrubbed_records: list[dict[str, Any]] = []
        for rec in records:
            scrubbed, rec_findings = scrub_record(rec, exact_tokens, secret_values)
            scrubbed_records.append(scrubbed)
            all_findings.extend(rec_findings)

        if all_findings:
            jsonl_path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in scrubbed_records) + "\n",
                encoding="utf-8",
            )

        # Second verification pass confirming zero residual matches
        residual_findings: list[dict[str, str]] = []
        for rec in scrubbed_records:
            _, res_f = scrub_record(rec, exact_tokens, secret_values)
            residual_findings.extend(res_f)

        print(
            json.dumps(
                {
                    "jsonl": jsonl_path.name,
                    "record_count": len(scrubbed_records),
                    "token_stats": token_stats,
                    "scrubbed_finding_count": len(all_findings),
                    "scrubbed_locations": all_findings,
                    "residual_finding_count": len(residual_findings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if len(residual_findings) == 0 else 1
    finally:
        exact_tokens.clear()
        secret_values.clear()


if __name__ == "__main__":
    sys.exit(main())
