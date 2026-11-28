# Phase 2 执行契约（方案 B：全量核心深扫 + P1/P2 轻量快筛）

> 本文档承接 `TASK_SPEC.md` 的全部第 0 节安全、只读、隔离与脱敏红线。所有产物仅写入 `C:\Users\shipi\AI-work\ke-arch-20261127\`，严禁改动 `C:\Users\shipi\vswp\BeikeMicroservice\` 下任何文件。

---

## 0. 前置加固：修复 `scripts/redaction_check.py` 脱敏正则
在落盘任何 Phase 2 产物前，必须先修复 `scripts/redaction_check.py` 中已发现的一处正则缺口：
- 当前 `cas_tgc_ticket` 写为 `\bTGC?-[A-Za-z0-9._-]{8,}\b`（仅匹配 `TG-`/`TGC-`，漏掉标准 CAS 的 `TGT-` 前缀）。
- **修改要求**：更新为 `\b(?:TGT|TGC?)-[A-Za-z0-9._-]{8,}\b`（同时保留 `cas_st_ticket` 的 `\bST-[A-Za-z0-9._-]{6,}\b`），并对全工作区（含 Phase 1 所有 `.json`/`.md` 及 Phase 2 全部新产物）重新执行扫描。
- 支持 `--output` 参数分别输出 `phase2a_redaction_check.json` / `phase2b_redaction_check.json`，扫描时跳过所有 `*_redaction_check.json` 自身。

---

## 1. 方案 B 总体分批结构（严格串行，防 429 与超时）
为控制单次任务时长与配额，方案 B 分为 2 个大阶段（当前先执行 **Phase 2A**）：

1. **Phase 2A（当前执行）**：
   - 开发通用、可复用的 Playwright 批量测绘器 `scripts/phase2_site_profiler.py`（同时支持公网直连与托管 SOCKS5 隧道、支持 `--resume` 断点续跑、支持多套异构认证模式）。
   - 执行 **Batch 2A（公网直连，不经 SOCKS5 隧道）**：目标从 `phase1_diff.md` 第 3.1 节与 `phase1_discovery.json` 动态解析，共计 **22 站**（5 个已验证公网基线 + 17 个高概率公网候选）。
   - 对本机直连发生网络重置、连接关闭、超时或内网 DNS 解析失败的站点，自动写入 `phase2a_handoff_to_2b.json`，留待 Phase 2B 走内网隧道接力复测。
2. **Phase 2B（待 Phase 2A 验收后启动）**：
   - **Step 2B-1（P1/P2 轻量 HTTP/SSO 快筛）**：通过托管 SOCKS5 隧道，对 `phase1_diff.md` 第 3.2 节中 P1（47 站）+ P2（42 站）共 89 站做低开销并发快筛，分类为 `accessible_or_spa` / `denied_403` / `login_required` / `unreachable_or_error`。
   - **Step 2B-2（内网 Playwright 深度测绘）**：复用 `scripts/phase2_site_profiler.py`（走托管 SOCKS5 隧道 + `--resume`），测绘「原 28 表其余 23 站 + P0 核心业务 39 站 + `phase2a_handoff_to_2b.json` 转入站 + Step 2B-1 筛出的 `accessible_or_spa` 站点」。

---

## 2. 通用测绘器 `scripts/phase2_site_profiler.py` 工程规范

### 2.1 输入与断点续跑
- 支持 CLI 参数：
  - `--targets-json <path>`：待测目标清单 JSON（每项含 `host`, `name`, `url`, `category`, `batch`, `auth_hint`）
  - `--output-jsonl <path>`：每测完 1 个站点立刻 `flush` 追加写入 1 行 JSON
  - `--resume`：读取已有 `--output-jsonl` 中已完成（且非临时网络中断）的 `host` 集合自动跳过
  - `--use-tunnel`：是否启用脚本内 `try...finally` 托管的临时 SSH SOCKS5 隧道（Batch 2A **不启用** `--use-tunnel`，以真实检验公网直连可达性；注意内存读取远端凭证仍通过只读 SSH 命令完成，读取完立即退出该短连接，不建立隧道）
  - `--timeout-ms 25000`：单站硬超时（默认 25 秒）

### 2.2 多模式认证装配（仅内存，绝不落盘）
- **默认 CAS + 业务 Cookie 模式**：按 `phase1_hub_discovery.py` 相同安全方式，将 `TGC` / `TGC_Secure` 注入统一登录域，并将现有业务 Cookie 注入对应业务父域，让站点自行完成官方 CAS 回调，不伪造任何角色或资料。
- **豌豆专属模式**（当目标主机匹配豌豆域名时）：在隔离上下文先访问该 origin 并仅向该 origin 的 `localStorage.token` 写入当前有效的 CRM 普租令牌，再导航至其聊天入口；同时观察 `/api/v1/users/me` 身份核验与菜单/接口结构。
- **Moma 轻应用托管模式**（当目标匹配轻应用平台路径时）：在隔离上下文带 TGC 访问，记录页面是否自动发起 CAS 换票、Supabase/Falcon 网关调用路径及页面可见模块，不修改任何数据、不调用写接口。
- **每个站点使用独立隔离的 `BrowserContext`**，防止跨站 Cookie/LocalStorage 相互污染。

### 2.3 安全与稳定性红线
- **全局禁登出拦截**：对每个 `BrowserContext` 注册路由拦截，凡路径命中 `/(logout|signout|logoff)(/|$|\?)` 一律 `route.abort()` 并计数记录。
- **严禁写操作**：全程仅导航与只读 DOM/网络观测，**不点击提交/保存/删除/发送按钮，不填表单，不发送聊天消息**。仅在页面加载完成后等待 `networkidle`（最长 6 秒）+ 2 秒静默缓冲，优先靠初始加载 DOM + Shadow DOM + XHR/Fetch 流量提取功能。
- **熔断机制**：在已知应认证通过的 5 个公网基线组中，若连续 3 个基线站全部跌回登录页，立即中止并记录告警，不反复刷登录页。

### 2.4 单站输出 Schema（`site_profiles_2a.jsonl` 每行结构）
每行必须为严格脱敏的 JSON 对象，包含：
```json
{
  "host": "example.internal",
  "name": "系统中文名",
  "category": "来源分类",
  "batch": "2A",
  "network_mode": "direct_local",
  "entry_url": "脱敏入口URL",
  "final_url": "脱敏终态URL",
  "redirect_hosts": ["跳转经过的主机去重列表"],
  "http_status": 200,
  "title": "页面标题(脱敏截断<=120字)",
  "access_verdict": "authenticated_verified | authenticated_page | denied_403 | login_required | direct_blocked_handoff_2b | error",
  "identity_match": true,
  "auth_mechanism": {
    "sso_type": "cas_redirect | cas_spa | localstorage_bearer | supabase_cas | independent_login | none | unknown",
    "cas_callback_paths": ["/cas"],
    "issued_cookie_names": ["仅Cookie名无值"]
  },
  "ui_modules": {
    "top_nav": ["去重清洗后的一级导航文本，最多25项，过滤姓名/工号/手机号等PII"],
    "side_nav_or_tabs": ["去重清洗后的侧栏/Tab/模块标题，最多30项"],
    "page_headings_or_cards": ["页面主标题/功能卡片名称，最多25项"],
    "shadow_dom_detected": false
  },
  "backend_apis": [
    {
      "method": "GET",
      "host": "api-host",
      "path": "/sanitized/path",
      "status": 200,
      "biz_code": 0
    }
  ],
  "backend_api_hosts": ["去重后的非静态API后端主机列表"],
  "errors": [],
  "elapsed_seconds": 4.2,
  "checked_at_utc": "ISO8601"
}
```
- **严格清洗 `ui_modules`**：自动剔除匹配当前用户姓名、工号、UCID、手机号、邮箱、门店具体个人信息的文本项，只保留通用功能/菜单名称。
- **过滤静态噪音**：`backend_apis` 仅记录 XHR/Fetch/Document 接口请求（排除 `.js/.css/.png/.jpg/.svg/.woff/.ico` 及纯埋点静态资源）。

---

## 3. Phase 2A 具体交付物与验收标准
完成 Phase 2A 后，工作目录 `C:\Users\shipi\AI-work\ke-arch-20261127\` 必须包含：
1. 更新修复后的 `scripts/redaction_check.py`（含 `TGT-` 规则）。
2. `scripts/phase2_site_profiler.py`（Phase 2A 与 2B 通用采集器）。
3. `phase2a_targets.json`（从 `phase1_diff.md` / `phase1_discovery.json` 生成的 22 站清单）。
4. `site_profiles_2a.jsonl`（22 站逐站脱敏功能与 API 档案）。
5. `phase2a_handoff_to_2b.json`（直连受阻、需转入 Phase 2B 内网隧道复测的站点列表及原因）。
6. `phase2a_summary.md`（中文汇报：22 站公网可达性与登录结论总表、每个可用站点的核心功能模块与后端 API 域解析、转入 2B 列表、脱敏自检证明）。
7. `phase2a_redaction_check.json`（全工作区扫描结果，必须 `passed == true` 且 `findings == []`）。
8. 确认 `BeikeMicroservice` 零修改、本机无残留临时隧道或孤儿浏览器进程。
