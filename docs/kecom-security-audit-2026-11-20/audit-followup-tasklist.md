# 安全审计后续整改 Tasklist（2026-11-24 修订版）

> **来源与范围说明**：本清单基于主报告（`kecom-security-audit-report-2026-11-20.md` r6）与 `round2`/`round3`/`round4` 台账交叉复核结果整理，**聚焦本次复核发现的纠偏项、脱敏门禁项与新增补盲项**。前期已登记的既有阻塞项仍以原台账为准（主报告 §5、三期报告 §3、四期报告 §6），并在文末执行节奏中作交叉引用。
> **脱敏声明**：本文档位于可分发审计目录内，所有待治理的本机/基础设施线索均以占位符代指，不含真实 IP、本机用户名路径或凭证原值。

> **执行状态（2026-11-24 r8 批次：离线收口 + round5 授权窗口 113 请求）**：
> - 离线已收口（随 r7）：**T2–T9、T17、T29、T30、T31**。
> - round5 授权窗口已闭环（随 r8 §3.8/§3.9，证据 `evidence/round5/` 6 份、113 请求 = 46+9+34+11+8+5）：**T1、T10、T13、T14、T15、T16、T21（独立立项 KE-33）、T25、T26**（共 9 项实测/复核完成）。
> - **T22 退回阻塞**：探针 v1 台账被 v2 同路径覆盖且无备份，round5 无有效配对证据，待重新扫码补采（详见主报告 §3.9）。
> - 部分完成：**T11/T12**（数据面已测，控制面/登录态函数鉴权阻塞）、**T18/T19/T20**（部分端点穿透已证，404 路径待平台白盒）、**T23**（TGC/豌豆/Moma 三项沿用二期 logout 完整配对证据；Supabase JWT/saas/trusteeship 联动待重新扫码补测）、**T24**（属性已补录，老接口信任排查待联网）。
> - 仍待专项/单独授权：**T27/T28**（AI Agent/Skill 沙箱，需受控 canary 域）、R4-B01~B07 dry-run 实跑（需授权手机号/窗口）。注：登出专项已销毁服务器会话，下轮需重新扫码。
> - 统一门禁现 60 文件 0 命中；证据总量 56 份 = 21+16+7+6+6，运行时实证 52 份。

---

## 一、主报告结论纠偏（6 项）

- [x] **T1｜KE-21 `prompts` canary 清理闭环存疑（P0，主报告 2.1 / 3.1 节）**　【r8 已闭环：精确回读 `0-1/2`、id-only 数组长度 2，坐实生产表残留 **2 条** canary，两次 `return=minimal` 的 204 DELETE 确为 RLS 静默拒绝；r7 口径收紧保留，运维按 name 标记清理 + 修 DELETE 策略列入 §6.1】
  - **现状**：视点 B 匿名态（`round2-anon-followup-a`）与视点 A 普通用户态（`round2-session-2026-11-23-read.json` 的 `KE21-user-insert-prompts-canary` / `KE21-user-delete-prompts-canary-cleanup`）均在 `POST 201` + `DELETE 204`（带 `Prefer: return=minimal`）后即认定“已完成清理闭环”。
  - **风险**：PostgREST 在 `return=minimal` 下，即使 RLS 拒绝删除（0 行受影响）也会返回 `204`；且匿名态写入 2 分钟后，`round2-anon-followup-b` 的 `KE21-count-exact-prompts` 从 `0-27/28`（28 行）变为 `*/29`（29 行）。若 `DELETE` 被 RLS 静默拒绝，生产 `prompts` 表中可能残留**最多 2 条**合成 canary（匿名 + 用户各 1 条）。
  - **动作**：补回读复查以确认生产表无 canary 残留；收紧结论口径——不单凭 `204` 认定匿名**或普通用户**拥有 `DELETE` 权限（与报告对 `admins PATCH 204` 的判定标准保持一致）。

- [x] **T2｜KE-25 / KE-29 解密接口信封头与零 UUID 行为精确拆分（主报告 2.1 / 3.1 vs 三期 2.3）**（r7 §2.1/§3.1 已按四场景拆写）
  - **修正口径（对照 `ke_round2_anon_followup_c.py` 与三期 `r3-decrypt-crosschat-2026-11-24.json`）**：
    1. **`chat_id` 正确 + 匹配密钥 + 行内 `rows[0].id` 为零 UUID**（`KE25-rows-list-dict-with-id`）→ `HTTP 200 / code=0`（不校验行归属）；
    2. **`chat_id` 正确 + 匹配密钥 + 乱填 `table_name`**（`KE25-rows-bad-table` / `D6`）→ `HTTP 200 / code=0`（不校验表名）；
    3. **`chat_id` 为零 UUID 或跨应用 + 带不匹配密钥**（`D2`/`D3`/`D4`）→ **`HTTP 400 / code=1`**（`X-Envelope-Key 与 chat_id 不匹配`），并非 `code=0`；
    4. **彻底省略 `X-Envelope-Key` 头**（`D5` / `followup-c`）→ **`HTTP 200 / code=0`**，且响应体从 `envelope` 密文信封退化为未封装的 `data` 数组（直接绕过绑定校验，为最核心的行为差异）。
  - **动作**：主报告按上述 4 类场景精确拆写，与三期结论对齐。

- [x] **T3｜KE-21 补写实测爆炸半径量级与 `phone` 形态说明（主报告 2.1 节 P0 摘要）**（r7 §2.1/§3.1 已写入 80,795 / 2,239,107 / 58,252 / 4,095 / 1 精确计数与 phone 长度 14 说明）
  - **精确计数（`round2-anon-followup-b`）**：`users` **80,795** 行、`usage_logs` **2,239,107** 行、`policy_news` 58,252 行、`new_buildings` 4,095 行、`admins` 1 行。
  - **附带修正**：`followup-c` 中 `users.phone` 长度为 **14**（非 11 位裸手机号），注明形态差异（可能含区号或掩码），避免与 `KE-01` 的 11 位数字形态混淆。

- [x] **T4｜严重/高危分组计数与全期证据总量口径修正（主报告第 2 节汇总段、1.1 节）**（r7 已按 15 行重算并把 KE-32 移出 8 根因；证据总量锁定 50 份，四期 4 份 dry-run 单列、不计运行时实证）
  - **分组计数**：严重/高危逐条核算共 15 行——`KE-01`(1) + `KE-02/03/22/30`(4) + `KE-04`(1) + `KE-05/06`(2) + `KE-18`(1) + `KE-21/27/28`(3) + `KE-24`(1) + `KE-25/29`(2)。`KE-32` 实际计入 8 个中危，故严重/高危括号内去掉 `/32`，避免误算为 16 行。
  - **证据总量**：1.1 节“脱敏证据总量 38 份”仅含一、二期（一期 21 份：20 JSON + 1 TXT；二期 17 份 JSON）。补齐三期（7 份 JSON）与四期（6 份 JSON，**须显式注明其中 4 份为 0 请求 `*-dryrun.json` 计划，不计入运行时实证量**）；最终归档总数在 **T7 落地后再锁定**（若移出 `round2-local-hardening-2026-11-23.json` 则二期为 16 份）。

- [x] **T5｜KE-21 RPC 超时证据索引精确到各自函数名（主报告 2.1 / 3.1 节）**（r7 已分别标注 `get_usage_overview` 8.21s 用户态与 `get_usage_overview_with_filter` 3.25s 匿名态）
  - 视点 A 用户态（`round2-session-2026-11-23-read.json`）：`rpc/get_usage_overview`（8.21s 超时，`57014`）；
  - 视点 B 匿名态（`round2-anon-2026-11-23.json`）：`rpc/get_usage_overview_with_filter`（3.25s 超时，`57014`）。
  - 同属匿名/低权限直达 DB 根因，但证据引用分别写清函数名。

- [x] **T6｜补录生产 GoTrue 默认回调 `localhost:3000` 缺陷（关联 KE-08，当前报告零提及）**（r7 已补入 §2.1 KE-08 行；`redirect_to` 白名单核查属联网项，随 T32）
  - **证据**：`round2-session-2026-11-23-read.json` 的 `app_token_exchange.terminal_redirect_host = "localhost:3000"`；`ke_round2_session.py` 注释印证默认回调为明文 `http://localhost:3000#access_token=...`。
  - **定性**：生产环境 GoTrue `SITE_URL` 残留本地开发配置缺陷。
  - **动作**：补入主报告并核查 GoTrue `redirect_to` 白名单限制。

---

## 二、脱敏与证据门禁盲区（3 项，外发前必须收口）

- [x] **T7｜移出或脱敏 `evidence/round2/round2-local-hardening-2026-11-23.json`**（已采用「移出」方案：迁入 gitignore 的 `outputs/security-local-workproduct/`，二期证据 17→16）
  - **问题**：该文件含非范围外发敏感信息——自建服务公网 IP（`<REDACTED_PUBLIC_IP>`）、工作站内网 IP（`<REDACTED_LAN_IP>`）、完整本机用户目录路径（`<USER_HOME>\...`）以及工作站全部 `0.0.0.0` 监听端口与 PID。
  - **动作**：该项已在主报告中划为“范围外”，移出可分发的 `evidence/` 目录（迁入本地工作产物目录），或对 IP、本机路径与端口/PID 全量脱敏。

- [x] **T8｜停用低熵敏感字段的无盐 `sha16` 脱敏（`followup-a` / `followup-c` 及后续脚本）**（历史台账已删 34 处低熵指纹、仅留类型/长度，UUID 与 `password_hash` 二次哈希保留；探针库改单次运行随机盐 HMAC（`hmac16`），低熵字段不输出任何哈希）
  - **问题**：对 `username`（5 字符）、`city`（3 字符）、`user_name`（2–3 字符）等低熵字段输出无盐 SHA-256 前 16 位，哈希空间极小，离线字典毫秒级可还原，实质等同未脱敏。
  - **动作**：清理历史台账中低熵字段的无盐 `sha16`（仅保留类型与长度）；后续探针脚本统一改用单次运行随机盐 HMAC，或对低熵字段不输出哈希。

- [x] **T9｜合并三套自检脚本为统一脱敏门禁，补齐扫描范围与规则**（统一为 `scripts/security/redaction_gate.py`，旧三脚本已删除；递归覆盖全目录 54 个 md/json/txt 含一期 20 JSON+1 TXT；新增邮箱、公网/内网 IPv4、本机路径、通用 64hex 规则，负向用例已验证可拦截）
  - **脚本整合**：合并 `round2/redaction_selfcheck.py`、`round3/redaction_selfcheck_r3.py`、`round4/r4_redaction_check.py` 为统一门禁脚本，防止三套正则口径漂移。
  - **范围补齐**：递归扫描整个 `docs/kecom-security-audit-2026-11-20/` 下的全部 `*.md`、`*.json`、`*.txt`——重点补入 `evidence/` 根目录的一期 21 份文件（**20 份 JSON + 1 份 TXT `wandou-param-shapes.txt`**）。
  - **规则补齐**：新增邮箱地址、公网/内网 IPv4、本机用户路径（`C:\Users\...` / `/home/...`）、以及通用 64 位管理员密码哈希字面量检测（现有规则仅覆盖 `x-signature` 后的 64 位 hex）。

---

## 三、测试覆盖面补盲（5 个维度，下一授权窗口执行）

### 3.1 Supabase 纵深攻击面（围绕 KE-21 严重根因）

- [x] **T10** 测应用 A `schema_migrations` 表匿名/普通用户可读性（含 `sql` 列；若可读将直接暴露建表 DDL、RLS 策略定义与 RPC 源码）。　【r8 已证风险：匿名 200 可读，`select=*` 1,888B 含 `sql` 列 1,809B；证据 `r5-anon-deep`】
- [ ] **T11** 评估 Supabase Storage 双平面权限：　【部分完成：r8 数据面两池 `GET /storage/v1/bucket` 均 200（边缘不拦）但匿名可见桶数 0，无桶可测对象级权限；**控制面 Moma 后端 RBAC 仍阻塞**（`/api/internal/supabase` 404，待白盒）】
  - **数据面**：`/falcon/supabase-volc/<APP_ID>/storage/v1/*` 桶的匿名与普通用户列举/读取/上传权限（线索：应用 B 存在 `_app_role_storage_rules`）；
  - **控制面**：Moma 后端 `/api/supabase/storage` 的普通员工 RBAC 门。
- [ ] **T12** 评估 Supabase Edge Functions 双平面鉴权：　【部分完成：r8 数据面两池 `functions/v1/` 匿名均 401、合成函数 404（匿名门有效）；真实函数名无法枚举，**登录态函数鉴权与控制面 RBAC 仍阻塞**】
  - **数据面**：`/falcon/supabase-volc/<APP_ID>/functions/v1/*` 匿名与普通用户调用鉴权；
  - **控制面**：Moma 后端 `/api/supabase/functions` 的普通员工 RBAC 门。
- [x] **T13** 逐个摸底 RPC 执行权限（重点排查 `SECURITY DEFINER` 函数残留的 `anon` / `authenticated` 执行授权）：　【r8 已证：A 池 12/14 匿名可达（6×200 含写 RPC `sync_usage_logs_city` + 6×57014 执行超时；仅 2 个 daily_trend 必填参数待定），B 池 8/8 全 200（含 `get_active_user_emails` 37KB、`get_user_usage_details` ≥64KB）；登录态同形无权限分化】
  - **写语义 RPC**：应用 A `sync_usage_logs_city`；
  - **敏感聚合读 RPC**：应用 A 其余 13 个 RPC（含 `get_top_users` 等）；应用 B 8 个 RPC（含 `get_active_user_emails`、`get_user_usage_details`、`_app_ehr_current_subordinates` 等）。
- [x] **T14** 确认 GoTrue 是否关闭外部匿名自主注册（`/auth/v1/signup`；若未关闭，外部攻击者可自助获取 `authenticated` JWT）。　【r8 已证：两池 `settings.disable_signup=false`，列 email/phone + 30 外部 IdP；未实际注册以免产生无法清理账号，CAPTCHA/审核待单点验证】
- [x] **T15** 核查关键业务表写权限：应用 A 的 `users`/`usage_logs`、应用 B 的 `properties`/`smart_clip_jobs` 是否允许匿名或普通用户写入（遵循零匹配/canary 清理红线）。　【r8 空 INSERT 分层：`users`/`admins` 42501 RLS 拒绝；`usage_logs`/`prompts`/`policy_news`（A）、`properties`/`smart_clip_jobs`（B）23502 穿过 RLS 仅 NOT NULL 拦截＝持 INSERT 权，匿名与员工同形】

### 3.2 同源多应用（UGC Lite App）横向隔离（三期 P1-4 深化）

- [x] **T16** 评估同源架构横向穿透风险：多租户/多作者 Lite App 均托管于同一源 `https://app-moma.ke.com`，共享同源 `localStorage`（`<appId>.supabase.auth.token`、`admin_session`）与 `path=/` 的 `ke_sess_bak`——任一 Lite App 存在 XSS 或恶意前端代码即可直接读取同域下其他全部 Lite App 令牌，无需突破 SOP。　【r7 §3.6 已交付风险评估（r3 静态证据：同源存储/同构 ke_sess_bak/无 CSP 隔离），结构性修复见 T17】
- [x] **T17** 在修复路线图中写入结构性隔离方案：单应用独立子域（如 `<appId>.app-moma.ke.com`）或强制 `sandbox` iframe 隔离；明确指出单纯添加 CSP 无法根治同源 UGC 互访。（r7 §6 P2/P3 路线图与 §3.6 已写入）

### 3.3 Moma 高危端点普通员工 RBAC 抽查（横向扩展 KE-26 线索）

- [ ] **T18** Sudo 特权模拟面：`/api/admin/sudo`（`M2-moma-userinfo` 响应结构出现 `sudo_active` 与 `real_ucid`）——审计普通员工 RBAC 拦截边界与操作审计留痕。　【部分完成：r8 员工态 GET 返 404（该后端无此路由，与匿名 401 形态不同），**是否在其他宿主/网关暴露待白盒**；sudo 留痕未测】
- [ ] **T19** `/api/internal/*` 内部接口群（`platform-keys`、`supabase`、`security-storage`、`auth` 等）——确认生产公网网关是否已彻底屏蔽路由，而非仅依赖应用层登录态兜底。　【部分完成：r8 匿名统一 401（框架登录门存在），员工态 4 条 internal 路径均 404；**404 不能等同网关已屏蔽，待平台白盒确认路由归属**】
- [ ] **T20** 运维/代理/调试/数据查询高危端点 RBAC：`/api/load-test/run`（潜在 DoS 放大器）、`/api/messages/_debug`（调试接口是否已在生产下线）、`/api/chats/proxy`（潜在 SSRF）、`/api/moma-data/hive`、`/api/moma-data/cli`、`/api/cache/redis`、`/api/offline/*`。　【部分完成：r8 实测 `load-test/run` GET 405+空 POST 422（路由存活、过登录门无管理 RBAC）、`chats/proxy` 200/errno=1（业务可达 SSRF 面）；`_debug`/hive/cli/redis/offline 员工态 404 待白盒】
- [x] **T21** 凭据类端点员工态鉴权：`/api/platform-api/iam-credential`、`/api/wechat/access-token`。　【r8 已证：普通员工有效会话下 `wechat/access-token` 200/errno=0 直发 `data.access_token`（137 字符，证据仅存形状）；`iam-credential` 422 过鉴权到参数校验；两者均无管理 RBAC 403，仅框架登录门】

### 3.4 会话注销闭环与遗留 Cookie

- [ ] **T22** 验证 Moma 自有 `POST /api/user/logout` 是否在服务端真正作废 `prd-assistant-token-prod`（还是仅下发 `Max-Age=0` 清浏览器 Cookie）。　【**阻塞（r8 台账事故）**：探针 v1（15 请求）曾观测到登出后旧 token 重放仍 200，但 v1 台账被 v2 同路径覆盖且无备份，归档的 v2（8 请求）会话起点已失效、`T22_moma_logout` 为空，round5 证据目录内无有效配对；T22 命题与 KE-24（CAS 联动）不同，不可用二期证据替代，待重新扫码后按带防覆盖命名的脚本补采】
- [ ] **T23** 补齐多子系统登出联动对照：核验 CAS `/logout` 后 `saas_token`（买卖域）、`lianjia_trusteeship_token`（托管域）以及文案 App 的 Supabase `access_token`/`refresh_token` 是否同步失效（阶段 4 仅覆盖 CAS `TGC`、豌豆 `puzu_lease_token` 与 Moma）。　【部分完成：TGC 与豌豆 puzu 吊销、Moma 不被联动吊销三项均以**二期 `round2-session-2026-11-23-logout.json` 完整 pre/post 配对**为准（round5 仅登出后快照辅助印证）；Supabase JWT/saas/trusteeship 因缺同端点登出前正对照而阻塞——托管域 `/house/detail/` 经证为公开壳（匿名/带 cookie 同 200），须重新扫码后用各域真实鉴权 API 补测】
- [ ] **T24** 将 `ke-authz-followup-2026-11-23.json` 记录的 4 个无 `HttpOnly`/`Secure` 遗留明文身份 Cookie（`_ucid_`、`_account_`、`_usercode_`、`_displayName_`）补录至主报告 7.1 属性表，并排查是否存在老接口信任这些客户端可篡改 Cookie。　【部分完成：r7 §7.1 已补录属性行；老接口信任排查为联网项，挂 T32】

### 3.5 前端供应链与 AI Agent 专项

- [x] **T25** SRI 完整性校验缺失：三期 `r3-surface-fetch` 显示应用壳引入多个 CDN 脚本（含安全/风控 SDK）但无 SRI 属性 → 纳入前端加固路线图。　【r8 复核：全部 CDN 脚本 0 个 `integrity`/`crossorigin`，已入 §6.1 P2】
- [x] **T26** 长缓存滞留风险：`/shipinshengcheng` HTML 壳设置了 `Cache-Control: public, max-age=2592000`（30 天），应急轮换密钥或修复前端漏洞后旧代码会在缓存中滞留 → 建议缩短 HTML 壳缓存或强制带构建指纹的短缓存策略。　【r8 复核属实：仅该壳 30 天，expert-shell/app-moma 根均 no-store；已入 §6.1 P2】
- [ ] **T27** 纳入 AI Agent 安全评估维度（OWASP LLM / Agentic）：评估知识库 RAG、`/api/skill/upload`、`/api/scheduled-tasks`、自定义 MCP 连接器与多步 Agent 工具调用链路中的**间接提示词注入（Indirect Prompt Injection）导致越权调用内部工具**风险。　【待专项：需受控 canary 域与工具链授权，已在 §6.1 列为待授权项】
- [ ] **T28** Skill 包解析与执行沙箱边界评估。　【待专项：同 T27，需 Skill 上传/执行环境授权】

---

## 四、修订与执行节奏（按严格依赖顺序）

- [x] **T29｜第 1 步：脱敏前置治理（先于合并 r7）**
  - 完成 **T7**（移出或脱敏 `round2-local-hardening-2026-11-23.json`）与 **T8**（清理低熵字段无盐 `sha16` 并改造脚本），锁定 `evidence/` 目录最终归档文件清单：50 份（21 + 16 + 7 + 6）。
- [x] **T30｜第 2 步：合并发布主报告 r7**
  - 并入三期核实结果（`KE-25/29` 4 场景精确口径、`P0-3` 平台脚手架系统性结论）与四期新增离线发现（`R4-S01~S04`）；
  - 落地 **T1–T6** 全部纠偏、补齐 **T24** 遗留 Cookie 属性表与 **T17** 同源结构性隔离方案，并按 T29 落地后的文件清单锁定全期证据总数（单列四期 4 份 dry-run 计划）。
- [x] **T31｜第 3 步：运行统一脱敏自检门禁（外发前强制卡点）**
  - 完成 **T9** 脚本整合与规则扩容，对整个 `docs/kecom-security-audit-2026-11-20/`（含 r7 主报告、本清单及一至四期全部 `*.json`/`*.txt`/`*.md`）执行全量扫描，0 命中后方可对外提交。
- [ ] **T32｜第 4 步：下一授权窗口执行包（增量补盲 + 既有阻塞项收口）**　【round5（r8）已执行第 1/2 包主体，剩余项如下】
  1. **Supabase 纵深与清理核验**：~~复查 `prompts` canary 状态 + `schema_migrations` / Storage / Functions / RPC 权限摸底（T1、T10–T15）~~ **r8 已完成**（T1 残留 2 条坐实、T10/T13/T14/T15 定性、T11/T12 数据面已测）；**剩余**：运维清理 2 条 canary 并修 DELETE 策略、T11/T12 控制面与登录态 Functions 鉴权；
  2. **Moma 高危端点普通员工 RBAC 抽查**：**r8 部分完成**——T21 坐实并立项 KE-33、load-test/chats-proxy 穿透已证；**剩余**：404 路径（`/api/internal/*`、`/api/admin/sudo`、`_debug`、hive/cli/redis/offline）平台白盒确认，KE-26 对齐 `script` 字段复测；
  3. **凭证恢复后的会话/浏览器闭环（须重新扫码）**：**T22** 按防覆盖脚本补采 Moma 自有登出吊销配对、**T23 残余**补 Supabase JWT/`saas_token`/`lianjia_trusteeship_token` 各域真实鉴权 API 的正对照+重放；并收口既有阻塞项——**主报告 §5**（KE-09 修复后 harness、M3 改 `PUT`、postMessage 补 Moma Cookie、WD-02 4 个孤儿对象运维清理、T24 老接口 Cookie 信任面）、**三期 §3**（Falcon 主站 Cookie-only 混用、计费 `X-App-Id` 配额、CORS 成功响应跨域读）、**四期 §6**（授权窗口实跑 `r4_*.py` 探针，R4-B01~B07）、**T27/T28**（Agent 间接注入与 Skill 沙箱）。
