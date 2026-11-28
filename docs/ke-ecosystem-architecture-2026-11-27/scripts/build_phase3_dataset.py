#!/usr/bin/env python3
"""Offline Phase 3 dataset aggregator and reconciliation engine.

Reads all sanitized Phase 1 & Phase 2 workspace outputs plus read-only historical
baselines (summary.json and kecom-security-audit-report-2026-11-20.md),
deduplicates the 22 + 72 + 72 = 166 profiling rows down to 156 unique hosts
(preserving dual-network direct vs intranet perspectives for the 10 overlapped hosts),
reconciles against the 17 P1/P2 HTTP-screened-out hosts, 15 historical not_counted
hosts, Category 2/4/5 hosts, computes 7-grade availability (A-G), cookie clusters,
cross-site backend API topologies, and 7-domain groupings, and writes the single
authoritative source of truth: final_ecosystem_dataset.json.

Strictly offline: 0 network requests, 0 SSH, 0 browser launches, 0 credential reads,
zero writes outside C:\\Users\\shipi\\AI-work\\ke-arch-20261127\\.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Input files in workspace
PHASE1_DISCOVERY_PATH = WORKSPACE_ROOT / "phase1_discovery.json"
PROFILES_2A_PATH = WORKSPACE_ROOT / "site_profiles_2a.jsonl"
PROFILES_2B_PATH = WORKSPACE_ROOT / "site_profiles_2b.jsonl"
PROFILES_2B_P1P2_PATH = WORKSPACE_ROOT / "site_profiles_2b_p1p2.jsonl"
SCREEN_P1P2_PATH = WORKSPACE_ROOT / "phase2b_p1p2_screen.json"
TARGETS_P1P2_ALL_PATH = WORKSPACE_ROOT / "phase2b_p1p2_targets.json"
TARGETS_P1P2_DEEP_PATH = WORKSPACE_ROOT / "phase2b_p1p2_deep_targets.json"
HANDOFF_2A_PATH = WORKSPACE_ROOT / "phase2a_handoff_to_2b.json"

# Read-only historical baselines (never modified)
READONLY_SUMMARY_PATH = Path(
    WORKSPACE_ROOT.parent / "crm-sso-entry-review-2026-11-26" / "summary.json"
)
READONLY_AUDIT_REPORT_PATH = Path(
    WORKSPACE_ROOT.parent / "kecom-security-audit-2026-11-20" / "kecom-security-audit-report-2026-11-20.md"
)

OUTPUT_DATASET_PATH = WORKSPACE_ROOT / "final_ecosystem_dataset.json"

# Grade C hosts among authenticated_page: verified manually against final_url, title,
# HTTP/API 401/400 status, timeouts, and empty auth/deploy shells.
GRADE_C_HOSTS: dict[str, str] = {
    "app-moma.ke.com": "Moma 轻应用托管根域空壳（跳转 moma.ke.com/deploy_404，需携带具体应用 UUID 路径访问）",
    "saas.a.ke.com": "A+ 客户端认证底座空壳（923B 极简 HTML，0 业务功能链接，仅负责 CAS 票据交换）",
    "hx.ke.com": "认证通过但无运营角色，自动跳转至 https://hx.ke.com/notauth",
    "xfp.ke.com": "CAS 认证通过并调用 i-wp.ke.com 权限/参数接口，但页面渲染错误提示（title=错误）",
    "yes-or-no.ke.com": "CAS 认证通过，调用 getUserPerms 返回 400/20003 后落入 /p/noPerms",
    "mdm.ke.com": "CAS 认证通过后直接跳转至 https://mdm.ke.com/401",
    "sis.lianjia.com": "CAS 认证通过后直接跳转至 http://sis.lianjia.com/401",
    "bpm.ke.com": "CAS 认证通过但该具体移动流程参数页面显示「暂时无法访问」",
    "bpm-m.ke.com": "重定向至 bpm.ke.com 且该离职移动流程参数页面显示「暂时无法访问」（域已合流）",
    "esghub.ke.com": "HTTP 307 滞留加载态且 who-am-i/config 等 5 个核心业务 API 返回 401",
    "shebao.ke.com": "CAS 认证通过但 POST /currentUserApi/currUser 返回 401（仅 taskCount 200），页面未渲染",
    "vprivacy.ke.com": "20s 沉降超时且未完成 CAS 跳转（sso=none），空标题/0 UI/0 API",
    "m-legal.ke.com": "20s 沉降超时，仅加载 #/contract 空壳与 title=法螺系统，未捕获业务 API",
    "finops.ke.com": "CAS 认证通过但滞留 ecs_console #/ssoLogin「加载中」二次 SSO 握手态",
    "rpm.ke.com": "CAS 认证通过并签发 agentic_token，但滞留 ecs_console #/ssoLogin「加载中」二次 SSO 握手态",
}

REDIRECT_CONSOLIDATIONS: dict[str, dict[str, str]] = {
    "ke.link.lianjia.com": {
        "target_host": "ke.deyoulife.com",
        "reason": "旧链家客源/共享池入口在内网 CAS 回调后直接重定向合流至 ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer",
    },
    "link.lianjia.com": {
        "target_host": "shouye.link.lianjia.com",
        "reason": "Link 主域名入口直接重定向合流至工作台首页 shouye.link.lianjia.com/shou/index",
    },
    "bpm-m.ke.com": {
        "target_host": "bpm.ke.com",
        "reason": "移动端离职流程域名重定向合流至 bpm.ke.com/portal/r/w",
    },
    "naturaloppty.ke.com": {
        "target_host": "oomp.ke.com",
        "reason": "旧二手自然商机展位入口经 CAS 跳转合流至商机运营管理平台 oomp.ke.com/#/naturalOppty/businessopp/focus/overview",
    },
    "app-moma.ke.com": {
        "target_host": "moma.ke.com",
        "reason": "轻应用根域裸访问 302/路由落回 moma.ke.com/deploy_404",
    },
}

DOMAIN_DEFINITIONS: list[dict[str, str]] = [
    {
        "key": "domain_1_property_newhouse_trade",
        "name": "1. 房源、新房与交易核心作业域（A+ / Link / 德佑 / 商办新版）",
    },
    {
        "key": "domain_2_customer_clue_im",
        "name": "2. 客源、线索、触达与协同通讯域",
    },
    {
        "key": "domain_3_commercial_rules_ops",
        "name": "3. 商业化展位、规则风控与运营工具域",
    },
    {
        "key": "domain_4_ai_data_intel",
        "name": "4. AI 原生平台、数据智能与分析域",
    },
    {
        "key": "domain_5_employee_hr_payroll_training",
        "name": "5. 员工门户、HR 人事、考勤薪酬与培训绩效域",
    },
    {
        "key": "domain_6_finance_asset_legal_approval",
        "name": "6. 财务费控、资产、法务合规与审批中台域",
    },
    {
        "key": "domain_7_rnd_culture_admin",
        "name": "7. 产研基建、企业文化与行政服务域",
    },
]

DOMAIN_NAME_BY_KEY = {d["key"]: d["name"] for d in DOMAIN_DEFINITIONS}

HOST_TO_DOMAIN: dict[str, str] = {
    # Domain 1: 房源、新房与交易核心作业域
    "shouye.link.lianjia.com": "domain_1_property_newhouse_trade",
    "link.lianjia.com": "domain_1_property_newhouse_trade",
    "house.link.lianjia.com": "domain_1_property_newhouse_trade",
    "listing-de.ke.com": "domain_1_property_newhouse_trade",
    "lease-pz.link.lianjia.com": "domain_1_property_newhouse_trade",
    "trusteeship.link.lianjia.com": "domain_1_property_newhouse_trade",
    "hetong.link.lianjia.com": "domain_1_property_newhouse_trade",
    "gte.lianjia.com": "domain_1_property_newhouse_trade",
    "user-ntcs.gte.lianjia.com": "domain_1_property_newhouse_trade",
    "intcms.jiaoyi.lianjia.com": "domain_1_property_newhouse_trade",
    "storm.link.lianjia.com": "domain_1_property_newhouse_trade",
    "ishop.lianjia.com": "domain_1_property_newhouse_trade",
    "ke-map.ke.com": "domain_1_property_newhouse_trade",
    "lead.ke.com": "domain_1_property_newhouse_trade",
    "loushu.lianjia.com": "domain_1_property_newhouse_trade",
    "mobile.lianjia.com": "domain_1_property_newhouse_trade",
    "link.fang.lianjia.com": "domain_1_property_newhouse_trade",
    "xinfang.a.ke.com": "domain_1_property_newhouse_trade",
    "deal.fang.lianjia.com": "domain_1_property_newhouse_trade",
    "cms.newhouse-bd.ke.com": "domain_1_property_newhouse_trade",
    "xfp.ke.com": "domain_1_property_newhouse_trade",
    "saas.a.ke.com": "domain_1_property_newhouse_trade",
    "e-contract.ke.com": "domain_1_property_newhouse_trade",
    "pan.fang.lianjia.com": "domain_1_property_newhouse_trade",
    "newcms.fang.lianjia.com": "domain_1_property_newhouse_trade",
    "price.lianjia.com": "domain_1_property_newhouse_trade",
    "morg.lianjia.com": "domain_1_property_newhouse_trade",
    "yezhu.link.lianjia.com": "domain_1_property_newhouse_trade",

    # Domain 2: 客源、线索、触达与协同通讯域
    "ke.deyoulife.com": "domain_2_customer_clue_im",
    "ke.link.lianjia.com": "domain_2_customer_clue_im",
    "clue.a.ke.com": "domain_2_customer_clue_im",
    "wandou.ke.com": "domain_2_customer_clue_im",
    "xiaoxi.link.lianjia.com": "domain_2_customer_clue_im",
    "kemcc.ke.com": "domain_2_customer_clue_im",
    "helper.ke.com": "domain_2_customer_clue_im",
    "xiaobei.ke.com": "domain_2_customer_clue_im",
    "m-jury.ke.com": "domain_2_customer_clue_im",
    "luoshu.ke.com": "domain_2_customer_clue_im",
    "haoma.lianjia.com": "domain_2_customer_clue_im",
    "notice.ke.com": "domain_2_customer_clue_im",
    "ncrm.lianjia.com": "domain_2_customer_clue_im",

    # Domain 3: 商业化展位、规则风控与运营工具域
    "brand.ke.com": "domain_3_commercial_rules_ops",
    "liulian.ke.com": "domain_3_commercial_rules_ops",
    "newhouse-cps.ke.com": "domain_3_commercial_rules_ops",
    "b.ke.com": "domain_3_commercial_rules_ops",
    "wrule.ke.com": "domain_3_commercial_rules_ops",
    "heimingdan.lianjia.com": "domain_3_commercial_rules_ops",
    "rulemanage.ke.com": "domain_3_commercial_rules_ops",
    "flint.lianjia.com": "domain_3_commercial_rules_ops",
    "limu.ke.com": "domain_3_commercial_rules_ops",
    "content.lianjia.com": "domain_3_commercial_rules_ops",
    "cms-tv.ke.com": "domain_3_commercial_rules_ops",
    "hx.ke.com": "domain_3_commercial_rules_ops",
    "8.lianjia.com": "domain_3_commercial_rules_ops",
    "naturaloppty.ke.com": "domain_3_commercial_rules_ops",
    "oomp.ke.com": "domain_3_commercial_rules_ops",
    "wi.ke.com": "domain_3_commercial_rules_ops",
    "lingjing.ke.com": "domain_3_commercial_rules_ops",

    # Domain 4: AI 原生平台、数据智能与分析域
    "letsai.ke.com": "domain_4_ai_data_intel",
    "moma.ke.com": "domain_4_ai_data_intel",
    "app-moma.ke.com": "domain_4_ai_data_intel",
    "is-prod.ke.com": "domain_4_ai_data_intel",
    "aiglasses.ke.com": "domain_4_ai_data_intel",
    "chatbi.ke.com": "domain_4_ai_data_intel",
    "chat-excel.ke.com": "domain_4_ai_data_intel",
    "speechhub.ke.com": "domain_4_ai_data_intel",
    "ketest-pro.ke.com": "domain_4_ai_data_intel",
    "odin.ke.com": "domain_4_ai_data_intel",
    "codin.ke.com": "domain_4_ai_data_intel",
    "data.ke.com": "domain_4_ai_data_intel",
    "biz-data.ke.com": "domain_4_ai_data_intel",
    "fast.ke.com": "domain_4_ai_data_intel",
    "compass.ke.com": "domain_4_ai_data_intel",
    "ai-sec.ke.com": "domain_4_ai_data_intel",
    "bella.ke.com": "domain_4_ai_data_intel",
    "jianli.ke.com": "domain_4_ai_data_intel",
    "opengpt.ke.com": "domain_4_ai_data_intel",
    "codelink.ke.com": "domain_4_ai_data_intel",
    "pref.merlin.lianjia.com": "domain_4_ai_data_intel",

    # Domain 5: 员工门户、HR 人事、考勤薪酬与培训绩效域
    "family.ke.com": "domain_5_employee_hr_payroll_training",
    "hr.ke.com": "domain_5_employee_hr_payroll_training",
    "recruit.ke.com": "domain_5_employee_hr_payroll_training",
    "resume-agc.ke.com": "domain_5_employee_hr_payroll_training",
    "profile.ke.com": "domain_5_employee_hr_payroll_training",
    "kaoqin.hr.ke.com": "domain_5_employee_hr_payroll_training",
    "xinchou.ke.com": "domain_5_employee_hr_payroll_training",
    "salary-middle.ke.com": "domain_5_employee_hr_payroll_training",
    "salary-tax.ke.com": "domain_5_employee_hr_payroll_training",
    "salary-monitor.ke.com": "domain_5_employee_hr_payroll_training",
    "lcm.ke.com": "domain_5_employee_hr_payroll_training",
    "nltx.ke.com": "domain_5_employee_hr_payroll_training",
    "jixiao.ke.com": "domain_5_employee_hr_payroll_training",
    "study.ke.com": "domain_5_employee_hr_payroll_training",
    "admin-huaqiao.ke.com": "domain_5_employee_hr_payroll_training",
    "m-intelligencecampus-hq.ke.com": "domain_5_employee_hr_payroll_training",
    "shebao.ke.com": "domain_5_employee_hr_payroll_training",
    "renshi.ehr.lianjia.com": "domain_5_employee_hr_payroll_training",
    "cb.ke.com": "domain_5_employee_hr_payroll_training",
    "xinchou-data.ke.com": "domain_5_employee_hr_payroll_training",
    "leader.ke.com": "domain_5_employee_hr_payroll_training",
    "hrlearn.ke.com": "domain_5_employee_hr_payroll_training",
    "admin-study.ke.com": "domain_5_employee_hr_payroll_training",
    "okr.ke.com": "domain_5_employee_hr_payroll_training",
    "report.ke.com": "domain_5_employee_hr_payroll_training",
    "profile-h5.ke.com": "domain_5_employee_hr_payroll_training",
    "inrecruit.ke.com": "domain_5_employee_hr_payroll_training",
    "spi.ke.com": "domain_5_employee_hr_payroll_training",
    "chip.ke.com": "domain_5_employee_hr_payroll_training",
    "ganbu.ke.com": "domain_5_employee_hr_payroll_training",

    # Domain 6: 财务费控、资产、法务合规与审批中台域
    "bfa.ke.com": "domain_6_finance_asset_legal_approval",
    "pay.ke.com": "domain_6_finance_asset_legal_approval",
    "budget-control.ke.com": "domain_6_finance_asset_legal_approval",
    "kepler.ke.com": "domain_6_finance_asset_legal_approval",
    "mzichan.ke.com": "domain_6_finance_asset_legal_approval",
    "contract.ke.com": "domain_6_finance_asset_legal_approval",
    "mknow.ke.com": "domain_6_finance_asset_legal_approval",
    "workflow.lianjia.com": "domain_6_finance_asset_legal_approval",
    "finops.ke.com": "domain_6_finance_asset_legal_approval",
    "rpm.ke.com": "domain_6_finance_asset_legal_approval",
    "mdm.ke.com": "domain_6_finance_asset_legal_approval",
    "sis.lianjia.com": "domain_6_finance_asset_legal_approval",
    "bpm.ke.com": "domain_6_finance_asset_legal_approval",
    "bpm-m.ke.com": "domain_6_finance_asset_legal_approval",
    "m-legal.ke.com": "domain_6_finance_asset_legal_approval",
    "vprivacy.ke.com": "domain_6_finance_asset_legal_approval",
    "caigou.ke.com": "domain_6_finance_asset_legal_approval",
    "zichan.ke.com": "domain_6_finance_asset_legal_approval",
    "shandian.lianjia.com": "domain_6_finance_asset_legal_approval",
    "case-legal.ke.com": "domain_6_finance_asset_legal_approval",
    "company-legal.ke.com": "domain_6_finance_asset_legal_approval",
    "legal-compliance.ke.com": "domain_6_finance_asset_legal_approval",
    "fssc.lianjia.com": "domain_6_finance_asset_legal_approval",
    "legal-lawyer-library.ke.com": "domain_6_finance_asset_legal_approval",

    # Domain 7: 产研基建、企业文化与行政服务域
    "apollo.portal.life.ke.com": "domain_7_rnd_culture_admin",
    "cloud.intra.ke.com": "domain_7_rnd_culture_admin",
    "easy.intra.lianjia.com": "domain_7_rnd_culture_admin",
    "risk-platform.ke.com": "domain_7_rnd_culture_admin",
    "its.ke.com": "domain_7_rnd_culture_admin",
    "its.lianjia.com": "domain_7_rnd_culture_admin",
    "wen.ke.com": "domain_7_rnd_culture_admin",
    "homeworld.ke.com": "domain_7_rnd_culture_admin",
    "mcg.ke.com": "domain_7_rnd_culture_admin",
    "vote-admin.ke.com": "domain_7_rnd_culture_admin",
    "yes-or-no.ke.com": "domain_7_rnd_culture_admin",
    "esghub.ke.com": "domain_7_rnd_culture_admin",
    "be-admin.ke.com": "domain_7_rnd_culture_admin",
    "jingyingshe-admin.ke.com": "domain_7_rnd_culture_admin",
    "kehome-cms.ke.com": "domain_7_rnd_culture_admin",
    "vm.ke.com": "domain_7_rnd_culture_admin",
    "app-fly.ke.com": "domain_7_rnd_culture_admin",
    "gongyi.ke.com": "domain_7_rnd_culture_admin",
    "homeworld-front.ke.com": "domain_7_rnd_culture_admin",
    "meeting-assistant.ke.com": "domain_7_rnd_culture_admin",
    "ones.ke.com": "domain_7_rnd_culture_admin",
    "admin-sunshine.ke.com": "domain_7_rnd_culture_admin",
    "new-meeting.ke.com": "domain_7_rnd_culture_admin",
}

UNIVERSAL_OR_TRACKING_COOKIES = {
    "lianjia_ssid",
    "lianjia_uuid",
    "secdn_server_bak",
    "crosSdkDT2019DeviceId",
    "BAIDUID",
    "BAIDUID_BFESS",
    "ab_bid",
    "ab_jid",
    "ab_jid_BFESS",
    "ab_sr",
    "BMAP_SECKEY",
    "SECKEY_ABVK",
    "JSESSIONID",
    "rememberMe",
    "SESSION",
    "digData",
    "session_id",
    "sajssdk_2015_cross_new_user",
    "sensorsdata2015jssdkcross",
    "[REDACTED_JWT]",
}

NAMED_PLATFORM_COOKIE_CLUSTERS: list[dict[str, Any]] = [
    {
        "cluster_id": "aplus_saas_base",
        "cluster_name": "A+ 统一作业底座与经纪人角色会话簇",
        "cookies": ["MAIN_JSESSIONID", "_lianjia_link_snid", "saas_token", "HOUSEJSESSIONID", "login_ucid"],
    },
    {
        "cluster_id": "intelligent_service_cluster",
        "cluster_name": "智能服务（小伯乐/闹海 is-prod.ke.com）嵌入式助手会话簇",
        "cookies": ["intelligentService_sid", "intelligentservice_token"],
    },
    {
        "cluster_id": "odin_bigdata_cluster",
        "cluster_name": "Odin 大数据与 BI 驾驶舱令牌簇",
        "cookies": ["odin_token", "dwh-datascreen_token"],
    },
    {
        "cluster_id": "moma_supabase_cluster",
        "cluster_name": "Moma 轻应用与 Supabase/GoTrue 会话簇",
        "cookies": [
            "prd-assistant-token-prod",
            "X_Access_Token",
            "MOMA_LANGUAGE",
            "X-Product-Type",
            "_ucid_",
            "_account_",
            "_usercode_",
            "_displayName_",
            "_userFullEmail_",
        ],
    },
    {
        "cluster_id": "deyoulife_dk_cluster",
        "cluster_name": "德佑/大客户统一认证与客源令牌簇",
        "cookies": ["business_token", "ke_dk_u_id", "ke_dk_u_name", "ke_do_dk_u_id"],
    },
    {
        "cluster_id": "culture_cg_cluster",
        "cluster_name": "企业文化与 CG 评选后台令牌簇",
        "cookies": ["cg_ke_com", "mcg_ke_lianjia_token"],
    },
    {
        "cluster_id": "newhouse_lianjia_token_cluster",
        "cluster_name": "新房与工具链 lianjia_token 共享簇",
        "cookies": ["lianjia_token", "login_redirect_url"],
    },
    {
        "cluster_id": "lease_trusteeship_im_cluster",
        "cluster_name": "普租、省心租托管与消息链路共享簇",
        "cookies": ["lianjia_trusteeship_token", "puzu_lease_token", "puzu_lease_token_secure", "MESSAGEJSESSION"],
    },
    {
        "cluster_id": "salary_middle_cluster",
        "cluster_name": "薪酬中台、基薪与税友共享令牌簇",
        "cookies": ["salary_middle_token"],
    },
    {
        "cluster_id": "beihaoxue_study_cluster",
        "cluster_name": "贝好学 / 贝壳学堂培训令牌簇",
        "cookies": ["renovation-token"],
    },
    {
        "cluster_id": "huaqiao_campus_cluster",
        "cluster_name": "花桥学堂与老左图书馆令牌簇",
        "cookies": ["huaqiao_token"],
    },
    {
        "cluster_id": "its_portal_cluster",
        "cluster_name": "IT 门户双域名共享令牌簇",
        "cookies": ["its_ke_token"],
    },
    {
        "cluster_id": "bpm_actionsoft_cluster",
        "cluster_name": "ActionSoft BPM 流程平台会话簇",
        "cookies": ["AWSSESSIONID", "aws_uid"],
    },
    {
        "cluster_id": "workbench_task_cluster",
        "cluster_name": "工作台与任务看板会话簇",
        "cookies": ["task-databoard-cookie"],
    },
    {
        "cluster_id": "oomp_businessopp_cluster",
        "cluster_name": "自然商机运营平台令牌簇",
        "cookies": ["oomp_token"],
    },
    {
        "cluster_id": "agentic_token_cluster",
        "cluster_name": "Agentic 流程/控制台关联令牌簇",
        "cookies": ["agentic_token"],
    },
]

GRADE_LABELS = {
    "A": "A：认证通过且主体核验",
    "B": "B：认证通过可用业务页",
    "C": "C：部分受限、无权提示或空壳",
    "D": "D：业务 RBAC 无权",
    "E": "E：边缘安全网关 HIP 拦截",
    "F": "F：独立认证孤岛 / 认证未完成回落登录",
    "G": "G：不可达或 404/5xx",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            records.append(json.loads(s))
    return records


def classify_grade(rec: dict[str, Any]) -> tuple[str, str]:
    host = rec["host"].lower()
    verdict = rec["access_verdict"]
    fhost = (urllib.parse.urlsplit(rec.get("final_url", "")).hostname or "").lower()
    if verdict == "authenticated_verified":
        return "A", "认证通过且完成员工主体核验（identity_match=true）"
    if verdict == "authenticated_page":
        if host in GRADE_C_HOSTS:
            return "C", GRADE_C_HOSTS[host]
        return "B", "HTTP 200 认证通过（或免登录公开门户直出）且呈现有效业务路由/功能接口"
    if verdict == "denied_403":
        if fhost in {"hip.ke.com", "hip.lianjia.com"}:
            return "E", f"被边缘安全网关 {fhost}/forbidden 拦截"
        return "D", "完成/经过 SSO 入口后触发业务侧 RBAC 无权限控制"
    if verdict == "login_required":
        return "F", "需独立认证或 CAS 回调未建立有效会话并回落登录页"
    return "G", f"传输不可达或 HTTP 错误（http_status={rec.get('http_status')}）"


def summarize_profile(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch": rec.get("batch"),
        "network_mode": rec.get("network_mode"),
        "entry_url": rec.get("entry_url"),
        "final_url": rec.get("final_url"),
        "http_status": rec.get("http_status"),
        "title": rec.get("title", ""),
        "access_verdict": rec.get("access_verdict"),
        "identity_match": rec.get("identity_match", False),
        "sso_type": rec.get("auth_mechanism", {}).get("sso_type", "unknown"),
        "cas_callback_paths": rec.get("auth_mechanism", {}).get("cas_callback_paths", []),
        "issued_cookie_names": rec.get("auth_mechanism", {}).get("issued_cookie_names", []),
        "backend_api_hosts": rec.get("backend_api_hosts", []),
        "backend_api_count": len(rec.get("backend_apis", [])),
        "errors": rec.get("errors", []),
        "elapsed_seconds": rec.get("elapsed_seconds"),
    }


def extract_audit_findings_summary(audit_md: str) -> dict[str, Any]:
    """Extract factual architecture references from historical read-only audit report."""
    hosts = sorted(
        {
            m.group(1).lower()
            for m in re.finditer(
                r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:ke\.com|lianjia\.com|ljcdn\.com))\b",
                audit_md,
                flags=re.I,
            )
            if not m.group(1).lower().startswith("evil.")
        }
    )
    return {
        "source_file": "../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md",
        "documented_internal_and_cdn_hosts": hosts,
        "key_architectural_facts": [
            "app-moma.ke.com 托管多租户轻应用，同源共享 localStorage 与 path=/ Cookie（ke_sess_bak），依托后端 /falcon/* 网关代理 Supabase PostgREST/GoTrue 与 AI 端点。",
            "prd-assistant-backend.ke.com 为 Moma 主站后端，通过 CAS 换发长期 prd-assistant-token-prod 会话 Cookie，且 CAS /logout 未联动吊销 Moma 会话（KE-24）。",
            "wandou.ke.com 采用 localStorage.token Bearer 模式，网关同时接受 CRM 普租 puzu_lease_token 与托管 lianjia_trusteeship_token（KE-04）。",
            "*.ttb.test.ke.com 云内网测试域解析至 RFC 6598 CGNAT 段，生产部分页面仍存在对测试域（如 rule-chat.ttb.test.ke.com）的运行时引用。",
            "imapi.lianjia.com 为通知中心 kemcc.ke.com 关联的 IM 消息网关；img.ljcdn.com 与 ods.lianjia.com 为图片 CDN 与对象存储能力 URL 宿主。",
        ],
    }


def main() -> None:
    p1 = json.loads(PHASE1_DISCOVERY_PATH.read_text(encoding="utf-8"))
    summary_raw = json.loads(READONLY_SUMMARY_PATH.read_text(encoding="utf-8"))
    audit_md = READONLY_AUDIT_REPORT_PATH.read_text(encoding="utf-8")

    recs_2a = load_jsonl(PROFILES_2A_PATH)
    recs_2b = load_jsonl(PROFILES_2B_PATH)
    recs_2b_p1p2 = load_jsonl(PROFILES_2B_P1P2_PATH)
    screen_p1p2 = json.loads(SCREEN_P1P2_PATH.read_text(encoding="utf-8"))
    p1p2_all_targets = json.loads(TARGETS_P1P2_ALL_PATH.read_text(encoding="utf-8"))
    p1p2_deep_targets = json.loads(TARGETS_P1P2_DEEP_PATH.read_text(encoding="utf-8"))
    handoff_2a = json.loads(HANDOFF_2A_PATH.read_text(encoding="utf-8"))

    # Verify exact input line counts
    assert len(recs_2a) == 22, f"Expected 22 in 2A, got {len(recs_2a)}"
    assert len(recs_2b) == 72, f"Expected 72 in 2B, got {len(recs_2b)}"
    assert len(recs_2b_p1p2) == 72, f"Expected 72 in 2B-P1P2, got {len(recs_2b_p1p2)}"

    confirmed_28_map: dict[str, dict[str, Any]] = {
        g["host"].lower(): g for g in summary_raw.get("groups", [])
    }
    not_counted_15_map: dict[str, dict[str, Any]] = {}
    for nc in summary_raw.get("not_counted", []):
        nh = (urllib.parse.urlsplit(nc.get("url", "")).hostname or "").lower()
        if nh:
            not_counted_15_map[nh] = nc

    cat1_set = {
        x["host"].lower()
        for x in p1["diff_analysis"]["categories"]["1_new_internal_web_candidates"]
    }
    cat3_set = {
        x["host"].lower()
        for x in p1["diff_analysis"]["categories"]["3_reappeared_page_only_unverified"]
    }

    # Index Phase 1 hub appearances
    hub_meta_by_host: dict[str, dict[str, Any]] = {}
    for hub in p1.get("hubs", []):
        hk = hub["hub_key"]
        hname = hub["hub_name"]
        for link in hub.get("extracted_links", []):
            lh = (link.get("host") or "").lower()
            if not lh:
                continue
            m = hub_meta_by_host.setdefault(
                lh, {"hub_keys": set(), "hub_names": set(), "categories": set(), "labels": set()}
            )
            m["hub_keys"].add(hk)
            m["hub_names"].add(hname)
            if link.get("category"):
                m["categories"].add(link["category"])
            if link.get("label") and link["label"] != lh:
                m["labels"].add(link["label"])

    p1p2_target_map = {t["host"].lower(): t for t in p1p2_all_targets}
    p1p2_screen_map = {r["host"].lower(): r for r in screen_p1p2.get("results", [])}

    # Merge 2A + 2B + 2B-P1P2 by host
    by_host: dict[str, dict[str, dict[str, Any]]] = {}
    for r in recs_2a:
        by_host.setdefault(r["host"].lower(), {})["2a"] = r
    for r in recs_2b:
        h = r["host"].lower()
        assert "2b" not in by_host.get(h, {}), f"Duplicate 2B host: {h}"
        by_host.setdefault(h, {})["2b"] = r
    for r in recs_2b_p1p2:
        h = r["host"].lower()
        assert "2b" not in by_host.get(h, {}), f"Duplicate 2B/P1P2 host: {h}"
        by_host.setdefault(h, {})["2b"] = r

    assert len(by_host) == 156, f"Expected 156 unique deep hosts, got {len(by_host)}"

    # Build unified 156 site records
    deep_sites: list[dict[str, Any]] = []
    for host in sorted(by_host):
        views = by_host[host]
        r_2a = views.get("2a")
        r_2b = views.get("2b")
        final_rec = r_2b if r_2b is not None else r_2a
        assert final_rec is not None

        grade, grade_reason = classify_grade(final_rec)
        final_url = final_rec.get("final_url", "")
        final_host = (urllib.parse.urlsplit(final_url).hostname or "").lower()

        if host in confirmed_28_map:
            source_cohort = "confirmed_28"
        elif host in cat1_set:
            source_cohort = "cat1_new_internal_web"
        elif host in cat3_set:
            source_cohort = "cat3_reappeared_page_only"
        elif host == "app-moma.ke.com":
            source_cohort = "not_counted_retest_app_moma"
        else:
            source_cohort = "other"

        if host in confirmed_28_map:
            priority = "baseline_28"
        elif host in p1p2_target_map:
            priority = p1p2_target_map[host]["priority"]
        elif final_rec.get("batch") == "2B-P0":
            priority = "P0"
        elif r_2a is not None:
            priority = "2A_candidate"
        else:
            priority = final_rec.get("batch", "")

        batches_tested = []
        if r_2a is not None:
            batches_tested.append(r_2a["batch"])
        if r_2b is not None:
            batches_tested.append(r_2b["batch"])

        if r_2a is not None and grade in ("A", "B") and r_2b is None:
            network_exposure = "public_direct_usable"
        elif r_2b is not None and grade in ("A", "B"):
            network_exposure = "intranet_only_usable"
        elif grade == "E":
            network_exposure = "hip_blocked"
        else:
            network_exposure = "restricted_or_unavailable"

        domain_key = HOST_TO_DOMAIN[host]
        domain_name = DOMAIN_NAME_BY_KEY[domain_key]

        # Cookies
        issued_cookies = final_rec.get("auth_mechanism", {}).get("issued_cookie_names", [])
        business_cookies = [c for c in issued_cookies if c not in UNIVERSAL_OR_TRACKING_COOKIES]

        # Screen metadata for P1/P2
        scr_rec = p1p2_screen_map.get(host)
        screen_verdict = scr_rec.get("verdict") if scr_rec else None
        is_screen_false_pass = bool(scr_rec and grade in ("C", "D", "E", "F", "G"))
        is_screen_hard_false_pass = bool(scr_rec and grade in ("D", "E", "F", "G"))

        # API hosts (excluding login)
        api_hosts = final_rec.get("backend_api_hosts", [])
        cross_site_api_hosts = [
            ah for ah in api_hosts if ah != host and ah not in {"login.ke.com", "login.lianjia.com"}
        ]

        hm = hub_meta_by_host.get(host, {})
        consolidation = REDIRECT_CONSOLIDATIONS.get(host)

        entry = {
            "host": host,
            "name": final_rec.get("name", host),
            "source_cohort": source_cohort,
            "in_confirmed_28": host in confirmed_28_map,
            "priority": priority,
            "batches_tested": batches_tested,
            "final_batch": final_rec.get("batch"),
            "final_network_mode": final_rec.get("network_mode"),
            "network_exposure": network_exposure,
            "direct_verdict": r_2a["access_verdict"] if r_2a else None,
            "intranet_verdict": r_2b["access_verdict"] if r_2b else None,
            "dual_network_tested": r_2a is not None and r_2b is not None,
            "direct_profile": summarize_profile(r_2a) if r_2a else None,
            "intranet_profile": summarize_profile(r_2b) if r_2b else None,
            "entry_url": final_rec.get("entry_url"),
            "final_url": final_url,
            "final_host": final_host,
            "http_status": final_rec.get("http_status"),
            "title": final_rec.get("title", ""),
            "final_access_verdict": final_rec.get("access_verdict"),
            "identity_match": final_rec.get("identity_match", False),
            "grade": grade,
            "grade_label": GRADE_LABELS[grade],
            "grade_reason": grade_reason,
            "usable_ab": grade in ("A", "B"),
            "reachable_abc": grade in ("A", "B", "C"),
            "redirect_consolidated_to": consolidation["target_host"] if consolidation else None,
            "redirect_consolidation_reason": consolidation["reason"] if consolidation else None,
            "business_domain_key": domain_key,
            "business_domain_name": domain_name,
            "sso_type": final_rec.get("auth_mechanism", {}).get("sso_type", "unknown"),
            "cas_callback_paths": final_rec.get("auth_mechanism", {}).get("cas_callback_paths", []),
            "issued_cookie_names": issued_cookies,
            "business_cookie_names": business_cookies,
            "redirect_hosts": final_rec.get("redirect_hosts", []),
            "backend_api_hosts": api_hosts,
            "cross_site_api_hosts": cross_site_api_hosts,
            "backend_apis": final_rec.get("backend_apis", []),
            "ui_modules": final_rec.get("ui_modules", {}),
            "errors": final_rec.get("errors", []),
            "elapsed_seconds": final_rec.get("elapsed_seconds"),
            "screen_verdict": screen_verdict,
            "is_screen_false_pass": is_screen_false_pass,
            "is_screen_hard_false_pass": is_screen_hard_false_pass,
            "hub_appearances": {
                "hub_keys": sorted(hm.get("hub_keys", set())),
                "hub_names": sorted(hm.get("hub_names", set())),
                "categories": sorted(hm.get("categories", set()))[:10],
                "labels": sorted(hm.get("labels", set()))[:12],
            },
        }
        deep_sites.append(entry)

    site_by_host = {s["host"]: s for s in deep_sites}

    # 17 P1/P2 screened-out hosts
    enter_2b2_set = set(screen_p1p2.get("enter_2b2_hosts", []))
    screened_out_17: list[dict[str, Any]] = []
    for r in screen_p1p2.get("results", []):
        h = r["host"].lower()
        if h not in enter_2b2_set:
            screened_out_17.append(
                {
                    "host": h,
                    "name": r.get("name", h),
                    "priority": r.get("priority"),
                    "entry_url": r.get("entry_url"),
                    "final_url": r.get("final_url"),
                    "http_status": r.get("http_status"),
                    "title": r.get("title", ""),
                    "screen_verdict": r.get("verdict"),
                    "cas_callback_seen": r.get("cas_callback_seen"),
                    "issued_cookie_names": r.get("issued_cookie_names", []),
                    "error": r.get("error"),
                }
            )
    assert len(screened_out_17) == 17

    # 15 historical not_counted hosts reconciliation
    not_counted_15: list[dict[str, Any]] = []
    for nh, nc in sorted(not_counted_15_map.items()):
        retested = nh in site_by_host
        not_counted_15.append(
            {
                "host": nh,
                "name": nc.get("name", nh),
                "url": nc.get("url"),
                "historical_verdict": nc.get("verdict"),
                "historical_final": nc.get("final", ""),
                "historical_error": nc.get("error", ""),
                "historical_reason": nc.get("reason", ""),
                "seen_in_phase1_hubs": nh in hub_meta_by_host,
                "retested_in_phase2": retested,
                "phase2_grade": site_by_host[nh]["grade"] if retested else None,
                "phase2_verdict": site_by_host[nh]["final_access_verdict"] if retested else None,
            }
        )
    assert len(not_counted_15) == 15

    # Cookie clusters aggregation
    cookie_to_hosts: dict[str, list[str]] = defaultdict(list)
    for s in deep_sites:
        for c in s["issued_cookie_names"]:
            cookie_to_hosts[c].append(s["host"])

    cookie_clusters: list[dict[str, Any]] = []
    clustered_cookie_set: set[str] = set()
    for cl in NAMED_PLATFORM_COOKIE_CLUSTERS:
        members = sorted({h for c in cl["cookies"] for h in cookie_to_hosts.get(c, [])})
        per_cookie = {c: sorted(cookie_to_hosts.get(c, [])) for c in cl["cookies"] if cookie_to_hosts.get(c)}
        clustered_cookie_set.update(cl["cookies"])
        cookie_clusters.append(
            {
                "cluster_id": cl["cluster_id"],
                "cluster_name": cl["cluster_name"],
                "cookie_names": cl["cookies"],
                "host_count": len(members),
                "hosts": members,
                "per_cookie_hosts": per_cookie,
            }
        )

    other_shared_cookies = {
        c: sorted(hs)
        for c, hs in sorted(cookie_to_hosts.items(), key=lambda x: (-len(x[1]), x[0]))
        if len(hs) >= 2 and c not in UNIVERSAL_OR_TRACKING_COOKIES and c not in clustered_cookie_set
    }

    # Cross-site backend API topology & newly discovered infrastructure hosts
    api_host_callers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in deep_sites:
        eps_by_host: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for ep in s["backend_apis"]:
            ah = (ep.get("host") or "").lower()
            if ah and ah != s["host"] and ah not in {"login.ke.com", "login.lianjia.com"}:
                eps_by_host[ah].append(
                    {
                        "method": ep.get("method"),
                        "path": ep.get("path"),
                        "status": ep.get("status"),
                        "biz_code": ep.get("biz_code"),
                    }
                )
        for ah, eps in sorted(eps_by_host.items()):
            api_host_callers[ah].append(
                {
                    "caller_host": s["host"],
                    "caller_name": s["name"],
                    "caller_grade": s["grade"],
                    "endpoint_count": len(eps),
                    "endpoints": eps,
                }
            )

    cat2_list = p1["diff_analysis"]["categories"]["2_documented_core_backend_gateway_or_lite"]
    cat2_host_set = {x["host"].lower() for x in cat2_list}
    newly_discovered_backend_hosts = sorted(
        ah for ah in api_host_callers if ah not in cat2_host_set and ah not in site_by_host
    )
    # Also note redirect-discovered infrastructure hosts not in entry set
    redirect_infra_hosts: dict[str, list[str]] = defaultdict(list)
    for s in deep_sites:
        for rh in s["redirect_hosts"]:
            rhl = rh.lower()
            if rhl not in site_by_host and rhl not in {"login.ke.com", "login.lianjia.com"}:
                redirect_infra_hosts[rhl].append(s["host"])

    abnormal_signals = [
        {
            "signal_type": "production_to_test_domain_call",
            "caller_host": "heimingdan.lianjia.com",
            "caller_name": "黑名单（贝壳规则）",
            "target_host": "rule-chat.ttb.test.ke.com",
            "endpoint": "GET /api/chat/fixed_script (HTTP 302)",
            "description": "生产入口 heimingdan.lianjia.com 在页面加载过程中向云内网测试/预发域 rule-chat.ttb.test.ke.com 发起请求",
        },
        {
            "signal_type": "production_to_test_domain_call",
            "caller_host": "hrlearn.ke.com",
            "caller_name": "HR价值流",
            "target_host": "test-login.ke.com",
            "endpoint": "GET /authentication/sdk/init (HTTP 200)",
            "description": "生产域 hrlearn.ke.com 在页面加载时请求测试认证域 test-login.ke.com/authentication/sdk/init",
        },
        {
            "signal_type": "cross_system_token_acceptance",
            "caller_host": "wandou.ke.com",
            "caller_name": "豌豆（CRM 普租 IM）",
            "target_host": "wandou.ke.com",
            "endpoint": "Bearer <puzu_lease_token / lianjia_trusteeship_token>",
            "description": "历史审计实证豌豆网关同时接受普租 puzu_lease_token 与托管 lianjia_trusteeship_token 作为有效 Bearer 凭据",
        },
        {
            "signal_type": "cas_logout_session_desync",
            "caller_host": "moma.ke.com",
            "caller_name": "Moma 轻应用平台",
            "target_host": "prd-assistant-backend.ke.com",
            "endpoint": "Cookie prd-assistant-token-prod (Expires=2083)",
            "description": "CAS /logout 吊销 TGC 后，prd-assistant-token-prod 独立重放仍返回 HTTP 200 / errno=0（KE-24）",
        },
    ]

    # Confirmed 28 review
    confirmed_28_items: list[dict[str, Any]] = []
    confirmed_28_grade_counts = Counter()
    for ch, grp in sorted(confirmed_28_map.items()):
        s = site_by_host[ch]
        confirmed_28_grade_counts[s["grade"]] += 1
        correction_notes: list[str] = []
        if s["grade"] == "D":
            correction_notes.append("业务 RBAC 无权限（实测 denied_403）")
        if ch == "saas.a.ke.com":
            correction_notes.append("确认为 923B 极简 CAS 票据交换空壳底座，非独立 Web 业务系统")
        if ch == "ke.link.lianjia.com":
            correction_notes.append("内网重定向合流至 ke.deyoulife.com，与德佑客源系统属同一后端实体")
        if ch == "naturaloppty.ke.com":
            correction_notes.append("重定向合流至 oomp.ke.com 且业务无权")
        confirmed_28_items.append(
            {
                "host": ch,
                "historical_name": grp.get("name"),
                "historical_grade": grp.get("grade"),
                "historical_direct_verified": grp.get("direct_verified"),
                "phase2_batch": s["final_batch"],
                "phase2_access_verdict": s["final_access_verdict"],
                "phase2_grade": s["grade"],
                "identity_match": s["identity_match"],
                "final_url": s["final_url"],
                "redirect_consolidated_to": s["redirect_consolidated_to"],
                "correction_notes": correction_notes,
            }
        )

    # P1/P2 stats
    p1p2_deep_sites = [s for s in deep_sites if s["final_batch"] == "2B-P1P2"]
    assert len(p1p2_deep_sites) == 72

    p1p2_by_priority_grade: dict[str, dict[str, int]] = {"P1": Counter(), "P2": Counter()}  # type: ignore
    p1p2_by_priority_verdict: dict[str, dict[str, int]] = {"P1": Counter(), "P2": Counter()}  # type: ignore
    p1p2_screen_to_prof: dict[str, dict[str, int]] = defaultdict(Counter)  # type: ignore
    p1p2_hard_false_passes: list[dict[str, Any]] = []
    p1p2_soft_c_false_passes: list[dict[str, Any]] = []

    for s in p1p2_deep_sites:
        pri = s["priority"]
        p1p2_by_priority_grade[pri][s["grade"]] += 1
        p1p2_by_priority_verdict[pri][s["final_access_verdict"]] += 1
        p1p2_screen_to_prof[f"{pri}:{s['screen_verdict']}"][s["final_access_verdict"]] += 1
        row = {
            "host": s["host"],
            "name": s["name"],
            "priority": pri,
            "screen_verdict": s["screen_verdict"],
            "final_access_verdict": s["final_access_verdict"],
            "grade": s["grade"],
            "http_status": s["http_status"],
            "title": s["title"],
            "final_url": s["final_url"],
            "grade_reason": s["grade_reason"],
            "errors": s["errors"],
        }
        if s["is_screen_hard_false_pass"]:
            p1p2_hard_false_passes.append(row)
        elif s["grade"] == "C":
            p1p2_soft_c_false_passes.append(row)

    # Global accounting & reconciliation
    grade_counts_all = Counter(s["grade"] for s in deep_sites)
    verdict_counts_all = Counter(s["final_access_verdict"] for s in deep_sites)
    public_sites = [s for s in deep_sites if s["network_exposure"] == "public_direct_usable"]
    intranet_usable_sites = [s for s in deep_sites if s["network_exposure"] == "intranet_only_usable"]
    all_usable_ab = [s for s in deep_sites if s["usable_ab"]]
    all_usable_ab_dedup = [
        s for s in all_usable_ab if s["redirect_consolidated_to"] is None
    ]
    confirmed_28_ab = [s for s in all_usable_ab if s["in_confirmed_28"]]
    confirmed_28_ab_dedup = [
        s for s in confirmed_28_ab if s["redirect_consolidated_to"] is None
    ]
    new_128_sites = [s for s in deep_sites if not s["in_confirmed_28"]]
    new_ab = [s for s in new_128_sites if s["usable_ab"]]
    new_ab_dedup = [
        s for s in new_ab if s["redirect_consolidated_to"] is None
    ]
    new_grade_counts = Counter(s["grade"] for s in new_128_sites)

    # Domain summary
    domain_catalog: list[dict[str, Any]] = []
    for ddef in DOMAIN_DEFINITIONS:
        dk = ddef["key"]
        d_sites = [s for s in deep_sites if s["business_domain_key"] == dk]
        d_ab = [s for s in d_sites if s["usable_ab"]]
        d_c = [s for s in d_sites if s["grade"] == "C"]
        d_defg = [s for s in d_sites if s["grade"] in ("D", "E", "F", "G")]
        domain_catalog.append(
            {
                "domain_key": dk,
                "domain_name": ddef["name"],
                "total_deep_sites": len(d_sites),
                "usable_ab_count": len(d_ab),
                "grade_a_count": sum(1 for s in d_sites if s["grade"] == "A"),
                "grade_b_count": sum(1 for s in d_sites if s["grade"] == "B"),
                "grade_c_count": len(d_c),
                "unavailable_defg_count": len(d_defg),
                "usable_hosts": [s["host"] for s in d_ab],
                "grade_c_hosts": [s["host"] for s in d_c],
                "unavailable_hosts": [s["host"] for s in d_defg],
            }
        )

    dataset: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": "phase3_v1",
        "accounting_reconciliation": {
            "raw_profile_rows": {
                "site_profiles_2a_jsonl": len(recs_2a),
                "site_profiles_2b_jsonl": len(recs_2b),
                "site_profiles_2b_p1p2_jsonl": len(recs_2b_p1p2),
                "raw_total_rows": len(recs_2a) + len(recs_2b) + len(recs_2b_p1p2),
                "overlap_2a_and_2b": len({r["host"] for r in recs_2a} & {r["host"] for r in recs_2b}),
                "overlap_2a_and_p1p2": 0,
                "overlap_2b_and_p1p2": 0,
                "unique_deep_profiled_hosts": len(deep_sites),
            },
            "global_candidate_closure": {
                "confirmed_28": len(confirmed_28_map),
                "cat1_new_internal_web_candidates": len(cat1_set),
                "cat3_reappeared_page_only_unverified": len(cat3_set),
                "retested_from_not_counted_app_moma": 1,
                "total_phase2_entered_hosts": len(deep_sites) + len(screened_out_17),
                "phase2_deep_profiled_hosts": len(deep_sites),
                "phase2_p1p2_screened_out_hosts": len(screened_out_17),
                "not_counted_remaining_unretested": sum(
                    1 for x in not_counted_15 if not x["retested_in_phase2"]
                ),
                "cat2_documented_core_backend_gateway_or_lite": len(cat2_list),
                "newly_discovered_backend_api_hosts": len(newly_discovered_backend_hosts),
                "cat4_aliases_of_confirmed_28": len(
                    p1["diff_analysis"]["categories"]["4_aliases_of_confirmed_28"]
                ),
                "cat5_external_third_party_saas": len(
                    p1["diff_analysis"]["categories"]["5_external_third_party_saas"]
                ),
            },
            "deep_156_verdict_counts": dict(sorted(verdict_counts_all.items())),
            "deep_156_grade_counts": {g: grade_counts_all.get(g, 0) for g in "ABCDEFG"},
            "availability_totals": {
                "usable_ab_hosts": len(all_usable_ab),
                "usable_ab_unique_entities_after_redirect_dedup": len(all_usable_ab_dedup),
                "redirect_consolidated_pairs_in_usable_ab": [
                    {
                        "alias_entry": s["host"],
                        "target_host": s["redirect_consolidated_to"],
                        "reason": s["redirect_consolidation_reason"],
                    }
                    for s in all_usable_ab
                    if s["redirect_consolidated_to"] is not None
                ],
                "reachable_abc_hosts": sum(grade_counts_all.get(g, 0) for g in "ABC"),
                "unavailable_defg_hosts": sum(grade_counts_all.get(g, 0) for g in "DEFG"),
                "public_direct_2a_tested": len(recs_2a),
                "public_direct_usable_ab": len(public_sites),
                "public_direct_reachable_abc": len(public_sites) + 1,  # + app-moma.ke.com (Grade C)
                "intranet_tunnel_tested": len(recs_2b) + len(recs_2b_p1p2),
                "intranet_only_usable_ab": len(intranet_usable_sites),
                "intranet_only_reachable_abc": len(intranet_usable_sites)
                + sum(
                    1
                    for s in deep_sites
                    if s["grade"] == "C" and s["final_network_mode"] == "socks5_tunnel"
                ),
            },
            "confirmed_28_reconciliation": {
                "original_confirmed_count": len(confirmed_28_map),
                "phase2_grade_counts": {g: confirmed_28_grade_counts.get(g, 0) for g in "ABCDEFG"},
                "usable_ab_entries": len(confirmed_28_ab),
                "unique_usable_entities_after_dedup": len(confirmed_28_ab_dedup),
                "correction_summary": {
                    "rbac_denied_grade_d": ["8.lianjia.com", "naturaloppty.ke.com"],
                    "empty_auth_shell_grade_c": ["saas.a.ke.com"],
                    "redirect_merged_entry": ["ke.link.lianjia.com -> ke.deyoulife.com"],
                },
            },
            "net_new_discovery_summary": {
                "newly_deep_profiled_hosts": len(new_128_sites),
                "new_grade_counts": {g: new_grade_counts.get(g, 0) for g in "ABCDEFG"},
                "new_usable_ab_entries": len(new_ab),
                "new_unique_usable_entities_after_dedup": len(new_ab_dedup),
                "new_usable_ab_by_batch": dict(
                    sorted(Counter(s["priority"] for s in new_ab).items())
                ),
            },
        },
        "phase2a_dual_network_comparison": [
            {
                "host": s["host"],
                "name": s["name"],
                "direct_verdict": s["direct_verdict"],
                "intranet_verdict": s["intranet_verdict"],
                "final_grade": s["grade"],
                "direct_http": s["direct_profile"]["http_status"] if s["direct_profile"] else None,
                "intranet_http": s["intranet_profile"]["http_status"] if s["intranet_profile"] else None,
                "intranet_final_url": s["final_url"],
            }
            for s in deep_sites
            if s["dual_network_tested"]
        ],
        "phase2b_p1p2_summary_stats": {
            "screen_total": screen_p1p2.get("total_targets"),
            "screen_verdict_counts": screen_p1p2.get("verdict_counts"),
            "screen_by_priority": screen_p1p2.get("by_priority"),
            "screened_out_count": len(screened_out_17),
            "deep_profiled_total": len(p1p2_deep_sites),
            "deep_by_priority_counts": {
                "P1": sum(1 for s in p1p2_deep_sites if s["priority"] == "P1"),
                "P2": sum(1 for s in p1p2_deep_sites if s["priority"] == "P2"),
            },
            "deep_by_priority_verdict": {
                pri: dict(sorted(cnt.items())) for pri, cnt in p1p2_by_priority_verdict.items()
            },
            "deep_by_priority_grade": {
                pri: {g: cnt.get(g, 0) for g in "ABCDEFG"}
                for pri, cnt in p1p2_by_priority_grade.items()
            },
            "screen_to_profile_matrix": {
                k: dict(sorted(v.items())) for k, v in sorted(p1p2_screen_to_prof.items())
            },
            "hard_false_pass_count": len(p1p2_hard_false_passes),
            "soft_c_false_pass_count": len(p1p2_soft_c_false_passes),
            "total_non_ab_count": len(p1p2_hard_false_passes) + len(p1p2_soft_c_false_passes),
            "usable_ab_count": sum(1 for s in p1p2_deep_sites if s["usable_ab"]),
            "hard_false_passes": p1p2_hard_false_passes,
            "soft_c_false_passes": p1p2_soft_c_false_passes,
        },
        "confirmed_28_review": confirmed_28_items,
        "deep_profiled_sites": deep_sites,
        "screened_out_p1p2": screened_out_17,
        "not_counted_from_summary": not_counted_15,
        "category_2_documented_core_backend_gateway_or_lite": cat2_list,
        "category_4_aliases_of_confirmed_28": p1["diff_analysis"]["categories"][
            "4_aliases_of_confirmed_28"
        ],
        "category_5_external_third_party_saas": p1["diff_analysis"]["categories"][
            "5_external_third_party_saas"
        ],
        "cookie_clusters": {
            "universal_or_tracking_cookie_counts": {
                c: len(cookie_to_hosts[c])
                for c in sorted(UNIVERSAL_OR_TRACKING_COOKIES)
                if c in cookie_to_hosts
            },
            "platform_clusters": cookie_clusters,
            "other_shared_cookies": other_shared_cookies,
        },
        "cross_site_api_topology": {
            "newly_discovered_backend_api_hosts": newly_discovered_backend_hosts,
            "redirect_only_infrastructure_hosts": {
                k: sorted(set(v)) for k, v in sorted(redirect_infra_hosts.items())
            },
            "api_host_callers": {k: v for k, v in sorted(api_host_callers.items())},
            "abnormal_architecture_signals": abnormal_signals,
        },
        "domain_catalog": domain_catalog,
        "historical_audit_context": extract_audit_findings_summary(audit_md),
        "phase2a_handoff_hosts": [t["host"].lower() for t in handoff_2a.get("targets", [])],
        "phase2b_p1p2_deep_target_count": len(p1p2_deep_targets),
    }

    OUTPUT_DATASET_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": OUTPUT_DATASET_PATH.name,
                "unique_deep_profiled_hosts": len(deep_sites),
                "grade_counts": dataset["accounting_reconciliation"]["deep_156_grade_counts"],
                "usable_ab_hosts": dataset["accounting_reconciliation"]["availability_totals"][
                    "usable_ab_hosts"
                ],
                "usable_ab_unique_entities": dataset["accounting_reconciliation"][
                    "availability_totals"
                ]["usable_ab_unique_entities_after_redirect_dedup"],
                "public_direct_usable_ab": dataset["accounting_reconciliation"][
                    "availability_totals"
                ]["public_direct_usable_ab"],
                "intranet_only_usable_ab": dataset["accounting_reconciliation"][
                    "availability_totals"
                ]["intranet_only_usable_ab"],
                "confirmed_28_usable_entities": dataset["accounting_reconciliation"][
                    "confirmed_28_reconciliation"
                ]["unique_usable_entities_after_dedup"],
                "net_new_usable_ab_entries": dataset["accounting_reconciliation"][
                    "net_new_discovery_summary"
                ]["new_usable_ab_entries"],
                "net_new_unique_usable_entities": dataset["accounting_reconciliation"][
                    "net_new_discovery_summary"
                ]["new_unique_usable_entities_after_dedup"],
                "p1p2_hard_false_passes": len(p1p2_hard_false_passes),
                "p1p2_soft_c_false_passes": len(p1p2_soft_c_false_passes),
                "newly_discovered_backend_api_hosts": newly_discovered_backend_hosts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
