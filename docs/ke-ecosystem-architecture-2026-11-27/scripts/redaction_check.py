#!/usr/bin/env python3
"""Offline redaction self-check scanner for ke-arch-20261127 deliverables.

Scans all .json, .jsonl, and .md files under the workspace root for leaked
credentials, tickets, JWTs, cookies/tokens, PII (phone, email, ID card, bank card),
and local private key paths. Never echoes matched values; records only metadata
and a truncated SHA-256 hash of each finding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

TARGET_EXTENSIONS = {".json", ".jsonl", ".md"}
SELF_OUTPUT_NAME = "phase1_redaction_check.json"

RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "cas_tgc_ticket",
        re.compile(r"\b(?:TGT|TGC?)-[A-Za-z0-9._-]{8,}\b"),
    ),
    (
        "cas_st_ticket",
        re.compile(r"\bST-[A-Za-z0-9._-]{6,}\b"),
    ),
    (
        "jwt_token",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{4,})?\b"
        ),
    ),
    (
        "bearer_token",
        re.compile(
            r"(?i)\bBearer\s+(?!\[REDACTED\b|<REDACTED)[A-Za-z0-9._\-+/=]{12,}"
        ),
    ),
    (
        "cookie_or_token_assignment",
        re.compile(
            r"(?i)\b(?:"
            r"TGC|TGC_Secure|security_ticket|saas_token|puzu_lease_token|"
            r"lianjia_trusteeship_token|business_token|prd-assistant-token-prod|"
            r"lianjia_ssid|lianjia_uuid|JSESSIONID|HOUSEJSESSIONID|MESSAGEJSESSION|"
            r"PHPSESSID|SESSIONID|bmgr-token|PC_CPS_TOKEN|signapptob_token|"
            r"renovation-token|xinchou_token|lianjia_kaoqin_login_token|"
            r"ke_sess_bak|access_token|refresh_token|credential|cookie"
            r")\s*[=:]\s*[\"']?"
            r"(?!\[REDACTED\b|<REDACTED|true\b|false\b|null\b|none\b|\{|\[)"
            r"([A-Za-z0-9._\-+/=%]{10,})"
        ),
    ),
    (
        "url_sensitive_query",
        re.compile(
            r"(?i)[?&#](?:ticket|token|code|access_token|refresh_token|signature|"
            r"credential|secret|password|auth|st|tgc)="
            r"(?!\[REDACTED\b|<REDACTED)[A-Za-z0-9._\-+/=%]{6,}"
        ),
    ),
    (
        "cn_mobile_phone",
        re.compile(r"(?<![\dA-Za-z_-])1[3-9]\d{9}(?![\dA-Za-z_-])"),
    ),
    (
        "email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "cn_id_card",
        re.compile(
            r"(?<![\dA-Za-z])"
            r"[1-6]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
            r"(?![\dA-Za-z])"
        ),
    ),
    (
        "bank_card_16_19",
        re.compile(r"(?<![\dA-Za-z.-])[3-6]\d{15,18}(?![\dA-Za-z.-])"),
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "local_private_key_or_cred_path",
        re.compile(
            r"(?i)(?:[A-Za-z]:\\|/)[^\s\"'<>]*(?:"
            r"(?:\.pem|\.key|\.p12|\.pfx)(?=$|[\s\"'<>?#:/])|id_rsa|id_ed25519|"
            r"\.ssh[\\/][^\s\"'<>]+|credential_store\.bin|beike-store-media\.env"
            r")"
        ),
    ),
    (
        "pii_json_field",
        re.compile(
            r"(?i)\"(?:ucid|Lianjia_BUcid|employee_principal|usercode|"
            r"phone|mobile|email|idCard|bankCard|realName|employeeName)\""
            r"\s*:\s*\"(?!\[REDACTED\b|<REDACTED|\s*\")([^\"]{2,})\""
        ),
    ),
    (
        "hex_secret_64",
        re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])"),
    ),
]


def _hash_match(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def scan_workspace(root: Path, output_path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scanned_files: list[str] = []

    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in TARGET_EXTENSIONS
        and p.resolve() != output_path.resolve()
        and not p.name.endswith("_redaction_check.json")
        and ".git" not in p.parts
        and "__pycache__" not in p.parts
        and ".venv" not in p.parts
    )

    for file_path in files:
        rel_path = file_path.relative_to(root).as_posix()
        scanned_files.append(rel_path)
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {
                    "file": rel_path,
                    "line": 0,
                    "rule": "unreadable_file",
                    "match_sha256_12": _hash_match(type(exc).__name__),
                }
            )
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            for rule_name, pattern in RULES:
                for m in pattern.finditer(line):
                    matched_str = m.group(1) if m.lastindex else m.group(0)
                    findings.append(
                        {
                            "file": rel_path,
                            "line": line_no,
                            "rule": rule_name,
                            "match_sha256_12": _hash_match(matched_str),
                        }
                    )

    return {
        "passed": len(findings) == 0,
        "scanned_files": scanned_files,
        "scanned_file_count": len(scanned_files),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline redaction checker for ke-arch-20261127 deliverables."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Workspace root directory to scan.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (defaults to <root>/phase1_redaction_check.json).",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    output_path = (args.output or (root / SELF_OUTPUT_NAME)).resolve()

    result = scan_workspace(root, output_path)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "passed": result["passed"],
                "scanned_file_count": result["scanned_file_count"],
                "finding_count": len(result["findings"]),
                "output": output_path.name,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
