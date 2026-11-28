# ke.com 房源与统一认证体系综合安全审查报告（2026-11-20 ~ 2026-11-24 最终合并版 r8）

> **唯一统一报告说明（r8）**：本文档整合一期统一报告（r1–r5）、2026-11-23 离线深挖补充、二期视点 B 匿名/静态/DOM 实证、二期视点 A（`beike-server`）单账号会话窗口、三期双应用匿名核实（70 请求）、四期离线深挖/dry-run 计划（0 实时请求）与五期授权窗口补盲（round5，113 请求，见 §3.8/§3.9）全部结果，原独立分报告与过程清单均已合并清理。三期、四期报告作为专题附件保留，编号映射以本报告为准。
> **范围与红线声明**：本报告仅聚焦 `ke.com` 房源与统一认证体系（Moma 主站、Moma 文案 App、豌豆 PC、CRM 租赁/买卖/托管、CAS SSO、图片 CDN），不包含自建连接器项目自身问题。全程仅使用只读查询、合成输入、随机 canary 与零匹配写探针；全文不含任何真实令牌、Cookie、ST、隐私或房源/客源原值，已通过统一脱敏门禁 `scripts/security/redaction_gate.py` 对整个审计目录（`*.md`/`*.json`/`*.txt`，60 个文件）全量扫描（0 命中，产物 `redaction-gate-selfcheck.json`）。

---

## 1. 执行概况与审查边界

### 1.1 探测视点与工作量总览
| 维度 | 内容与计数 |
| --- | --- |
| **视点 A（`beike-server`，云主机出口）** | 一期 20 条 primary 认证对照（文件内 28 条探针）+ 令牌注入/旁路/staging 测绘 + 单账号最小轮 37 请求 + POST 修正 3 请求 + 同日跟进矩阵；二期会话窗口 **71 条 urllib 请求**（`read` 26、`write` 21、`burst` 6、`sms` 0、`logout` 7、`cleanup-probe` 11）+ 浏览器 harness 2 轮（`csrf` / `csrf-rerun2`）。 |
| **视点 B（办公网工作站）** | 二期匿名与公开应用签名面 **95 条请求**（单次无重试）；三期双应用匿名核实 **70 条请求**（surface 10 + runtime 39 + 解密/RLS 13 + Expert 4 + Cookie 4，单次无重试）；本机 Chrome headless CDP 本地 `file://` 水槽实证 1 次；对 `*.ttb.test.ke.com` 及 `*.link.lianjia.com` 网络策略不可达。 |
| **离线静态复核（0 网络请求）** | 一期前端静态核查 **39/39 通过** + 二期离线静态核查 **28/28 通过**；四期 16 个 bundle/HTML 共 628 切片离线特征提取；REA AST 静态分析未执行目标 JS。 |
| **四期 dry-run 计划（0 实时请求）** | 4 份 `*-dryrun.json` 探针计划（短信 12 项 / IDOR 39 项 / CAS 19 项 / 限流 136 项），真实执行需显式环境变量解锁且受每端点 ≤12 次硬上限约束，**不计入运行时实证量**。 |
| **五期授权窗口（round5，2026-11-24）** | 凭证恢复后 **113 条请求**（按 6 份台账逐份核对：46+9+34+11+8+5）：视点 B 匿名面 **55** 条（Supabase 两池纵深 T1/T10–T15、Storage/Functions 数据面）+ 视点 A 登录态非破坏 **45** 条（用户面 RPC/写权、Moma 高危端点员工 RBAC T18–T21）+ 登出专项 **13** 条（`r5-logout-revoke` 8 + 只读补采 `r5-logout-confirm` 5），单次无重试、读为主、空对象写不产生真实行；详见 §3.8/§3.9 与 `evidence/round5/` 6 份台账。 |
| **脱敏证据总量（r8 锁定）** | **56 份**：一期 21 份（20 JSON + 1 TXT，`evidence/` 根目录）+ 二期 **16** 份 JSON（`evidence/round2/`）+ 三期 7 份 JSON（`evidence/round3/`）+ 四期 6 份 JSON（`evidence/round4/`，其中 4 份为 0 请求 dry-run 计划）+ 五期 6 份 JSON（`evidence/round5/`，113 请求，含登出专项 2 份）。运行时实证 52 份（扣除四期 4 份 dry-run）。二期原 `round2-local-hardening-2026-11-23.json` 含工作站 IP/端口/PID 与本机路径，属范围外本地加固产物，已移至 gitignore 的 `outputs/security-local-workproduct/`；历史遗留的 2 处脱敏漏报（`logout.json` 的 ST 原值、`cleanup-probe.json` 的明文 `fileId`）已完成形状脱敏，全库零原值残留。 |
| **当前凭证状态（r8 更新）** | 五期登出专项执行后服务端 TGC 已失效（重新换票仅返回 CAS 登录页、无 ST 签发），`credential_store.bin` 中的静态令牌（puzu/saas/trusteeship 等）亦已过记录有效期；**登录态探针（视点 A）须重新扫码恢复凭证库后才能再跑**，匿名面（视点 B，公开 anon JWT）不受影响。 |

### 1.2 口径与红线约束
* **三档判定口径**：统一使用 **风险成立** / **防线有效** / **阻塞**。未命中目标业务逻辑、脚本取证被污染、缺少正对照或开关未开的项一律记 **阻塞**，绝不并入通过。
* **注入术语区分**：一期 `injection-*.json` 均为**令牌/凭证注入**（持自身有效凭证换端点/换受众）；经典**输入注入**仅在二期完成 PostgREST 排序/过滤及搜索建议反射负对照（均安全），服务端渲染/Groovy 登录态门内输入注入仍阻塞。
* **单账号限制**：全程仅 1 个授权员工账号，所有依赖第二账号的横向 BOLA/IDOR、跨店/跨城访问均按红线搁置，不作「IDOR 不成立」结论。

---

## 2. 全局风险总览矩阵（35 项条目 / 9 个严重高危根因）

> **统计汇总（r8 核算口径）**：共 **35 行矩阵条目**——严重/高危 **16 行**（r7 核算 15 行：`KE-01`(1) + `KE-02/03/22/30`(4) + `KE-04`(1) + `KE-05/06`(2) + `KE-18`(1) + `KE-21/27/28`(3) + `KE-24`(1) + `KE-25/29`(2)；r8 新增 **`KE-33`(1)** 凭据端点员工越权），归并 **9 个独立根因**（新增 `KE-33`；`KE-32` 为中危，不计入本组）；中高/中危 **8 行**（`KE-07`、`KE-08`、`KE-10a`、`KE-19`、`KE-23`、`KE-31`、`KE-32`、`WD-02`）；低危 **6 行**（`KE-11~16`）；信息 **1 行**（`KE-17`）；加固项 **1 行**（`KE-20`）；待证/阻塞 **3 行**（`KE-09`、`KE-26`、`WD-01`）。四期 `R4-S01~S04`/`R4-B01~B07` 与五期 T10–T26 补盲不另立矩阵行（除 KE-33 外），按编号挂靠或见 §3.7/§3.8。

### 2.1 P0 紧急止血（严重 / 高危·已实证）
| 编号 | 资产 | 核心发现摘要 | 评级 | 实证状态 |
| --- | --- | --- | --- | --- |
| **KE-21 / KE-27 / KE-28** | `app-moma.ke.com` | **【升级为严重·匿名+用户双面坐实】** `/admin` 仅验可伪造的 `localStorage.admin_session`；`/admin/password-debug` 路由层已 302→`/deploy_404`，但**匿名（anon JWT）与普通登录用户**均已实证经 PostgREST 直读 `admins.password_hash`（64 位 hex 单次无盐 SHA-256）、`users` PII（`phone/ucid/email/org`）、`usage_logs` 及 59KB 根 OpenAPI（9 表 14 RPC）。**实测爆炸半径（HEAD `Prefer: count=exact`，T3 精确计数）**：`users` **80,795** 行、`usage_logs` **2,239,107** 行、`policy_news` 58,252 行、`new_buildings` 4,095 行、`admins` 1 行；`prompts` 计数在匿名 canary 写入后由 28 行变为 **29 行**。RPC 直达 DB 超时双面函数名不同（T5）：用户态 `rpc/get_usage_overview`（8.21s，`57014`）、匿名态 `rpc/get_usage_overview_with_filter`（3.25s，`57014`）。**写权与清理存疑（T1 口径收紧）**：匿名与用户各持一次 `prompts` 合成 canary 的 `POST 201`，随后 `DELETE`（`Prefer: return=minimal`）均返回 `204`；但 PostgREST 在 `return=minimal` 下即使 RLS 静默拒绝（0 行受影响）同样返回 `204`，故**不单凭 204 认定任何一面拥有 DELETE 权限**（与 `admins` 零匹配 PATCH 204 同判定标准），生产表可能残留最多 2 条合成 canary，补回读复查列入下一授权窗口（T32）。`admins` INSERT 被 RLS `42501` 拦截；零匹配 PATCH 仍无法区分静默拒绝与 0 命中。另：`users.phone` 实测字段长度 **14**（非 11 位裸手机号形态，可能含区号或掩码，勿与 KE-01 的 11 位数字形态混淆）。**r8 round5 纵深扩面（§3.8）**：① **T10** `schema_migrations`（含 `sql` 列，1,809B）匿名 200 可读，DDL/RLS/RPC 定义泄露；② **T13** RPC 匿名执行面 A 池 12/14、B 池 8/8 可达（含写 RPC `sync_usage_logs_city`、PII 聚合 `get_active_user_emails` 37KB / `get_user_usage_details` ≥64KB）；③ **T14** 两池 GoTrue `disable_signup=false`；④ **T15** 仅 `users`/`admins` 的 INSERT 被 RLS 拒（42501），`usage_logs`/`prompts`/`policy_news`/`properties`/`smart_clip_jobs` 五表匿名与员工均持 INSERT 权（23502 分层证实）。 | **严重** | 视点 B 匿名 + 视点 A 用户双面已证；r8 回读确认 canary 残留 2 条待运维清理 |
| **KE-01** | `prd-assistant-backend.ke.com` | Moma 员工目录 `/api/aroute/users/page` 普通员工可查 **63 字段**人事档案结构（`mobile` 呈 11 位数字形态），全程无 429 限流（`totalCount` 仅查询命中上界，存在分页重叠，不做人数外推）。 | **高** | 视点 A 已证 |
| **KE-18 / KE-32** | `lease-pz` / `bms.lease` / `trusteeship` / `prd-assistant-backend` / `app-moma` | **CORS 任意 Origin 反射 + 凭证跨域读 + 缓存投毒面**：CRM 三域对任意 Origin（含 `null`）反射 `ACAO` + `ACAC:true`，`lease-pz` 房源列表已证带会话跨域读 PoC。**视点 A 复核实证**：`prd-assistant-backend` 与 `app-moma` **8/8 探针全部反射 `evil.example` + `ACAC:true`**；缓存投毒维度 **5 条含 `Vary: Origin`（防线有效）**、**3 条 GET 缺 `Vary: Origin`（风险成立，KE-32）**；其中 `CORS-A-moma-backend-get-session` 带会话返回完整 `user_info`（200 / `errno=0`）且无 `Cache-Control: no-store`，双重命中带凭证跨域读与缓存投毒条件（与一期「不反射」冲突，见 3.3 节）。 | **高**（KE-18）<br>**中**（KE-32） | 视点 A + 视点 B 双面已证 |
| **KE-02 / KE-03 / KE-22 / KE-30** | `app-moma.ke.com` | **公开应用签名机制 + 重放/篡改 + 算力盗刷/熔断缺失**：前端公开 HMAC 秘密（实为 JWT 头常量 `{"alg":"HS256","typ":"JWT"}`）替代个人鉴权，匿名可读普租/新房房源及 `roles` 字段（数据密级待业务确认），并可匿名调用大模型对话（`max_tokens=1` 复证）、AI 生图、联网检索；二期实证 **Nonce 不去重**（同签名双 200 重放）、**未签名 query 参数篡改放行**，且 **M4 应用级 / 单出口 IP 并发熔断缺失**（5 路并发 `chat/completions` 全 200，无 429）。服务端对降级占位串与过期时间戳已 401 拦截。 | **高** | 视点 A + 视点 B 已证 |
| **KE-25 / KE-29** | `app-moma.ke.com` | **【攻击面已坐实·r7 按四场景精确拆分（T2，与三期 D1–D6 对齐）】** `/falcon/supabase-crypto/decrypt` 使用公开 App ID 派生 AES-GCM 密钥，无个人鉴权、不校验表名/行归属：① **chat_id 正确 + 匹配密钥 + 行内零 UUID** → `HTTP 200 / code=0`（不校验行归属）；② **chat_id 正确 + 匹配密钥 + 乱填 table_name** → `HTTP 200 / code=0`（不校验表名）；③ **chat_id 为零 UUID/跨应用 + 不匹配密钥** → **`HTTP 400 / code=1`**（`X-Envelope-Key 与 chat_id 不匹配`，并非 `code=0`）；④ **彻底省略 `X-Envelope-Key` 头** → **`HTTP 200 / code=0`**，响应体从 `envelope` 密文信封退化为未封装的 `data` 数组——绑定校验被直接绕过，为最核心行为差异。匿名与普通登录用户双面均证；未提交真实密文，真实明文解出需业务方提供可销毁密文 fixture。 | **高** | 视点 B 匿名 + 视点 A 用户双面已证（三期第二应用原样复现） |
| **KE-33（r8 新增，T21）** | `prd-assistant-backend.ke.com` | **凭据类端点普通员工无管理 RBAC 即可领取第三方访问凭据**：有效员工会话（正对照 `/api/user/info` errno=0）下，`GET /api/wechat/access-token` 返回 **200/errno=0，`data.access_token`（137 字符）+ `expires_at`**——普通员工可直接领取微信访问凭据（证据仅存键名/类型/长度，无原值）；同面 `GET /api/platform-api/iam-credential` 返 422（穿过鉴权到参数校验，补齐参数可达）。抽样 6 条路径的匿名基线均为 200/errno=401（框架登录门存在），但登录后无管理 RBAC 403 分层。 | **高** | 视点 A 登录态已证（`r5-session-probe2`）；凭据领取留痕与 IAM 参数面待补 |

### 2.2 P1 高优加固（高危 / 中高危 / 中危）
| 编号 | 资产 | 核心发现摘要 | 评级 | 实证状态 |
| --- | --- | --- | --- | --- |
| **KE-24** | `prd-assistant-backend.ke.com` | **【升级为高危·登出实证】** Moma 正式会话 Cookie `prd-assistant-token-prod` 重签发事件缺 `Secure` 且客户端 `Expires` 远至 2083-10-16；**视点 A 阶段 4 实证**：执行 CAS `/logout` 并吊销 `TGC` 后，独立重放登出前备份的旧 `prd-assistant-token-prod` 仍 HTTP 200 / `errno=0` 返回完整 `user_info`，响应体 SHA-256（`341ef410…54d9`）与登出前完全一致，存在**登出会话失效不一致**。 | **高** | 视点 A 登出独立重放已证 |
| **KE-04** | `wandou.ke.com` | 豌豆跨应用令牌受众混淆：网关同时接受 CRM 普租 `puzu_lease_token` 与托管 `lianjia_trusteeship_token` 作为 Bearer 凭据读取客源/房源（反向 Moma 拒绝）；前端存 `localStorage.token`（存储位置已证，窃取路径未证）。 | **高** | 视点 A 已证 |
| **KE-05 / KE-06** | `login.ke.com` / 全 SSO | **同根因两症状（合 1 个根因）**：单 `TGC` 无需设备指纹与风控 Cookie 即可静默通兑全部子系统，且 `TGC` 与业务令牌无 IP/设备绑定、异机异网直接可用。**已关闭子项**：视点 A 独立重放实证 CAS `/logout` 能正确吊销服务端旧 `TGC`（防线有效）。 | **高** | 视点 A 已证（吊销已证有效） |
| **KE-31** | `moma.ke.com` | 主站 `MessageCard` 与 `MessageCenterDrawer` 两处将服务端 `content` 仅做 `\n→<br>` 后直赋 `innerHTML`，三应用 bundle 均无 DOMPurify；本机 Chrome headless CDP 实证 `img onerror`、`svg onload`、`iframe srcdoc`、`javascript:` 链接等 6 类载荷执行（存储投递链阻塞）。 | **中高** | 离线静态 + 本地 CDP 实证 |
| **WD-02** | `wandou.ke.com` | **【二期新增】** `/api/v2/note-files/upload` 无扩展名与路径穿越校验：`.html`、`.svg`、`round2.php.jpg`、`../../round2.txt` 四类载荷全部 HTTP 200 落对象存储；除双扩展名成功经 `create-image` 绑定并删除外，其余被拒且平台无任何用户可达的孤儿文件删除接口（4 个合成 canary 残留待对象存储 GC/运维清理）。`ods.lianjia.com` 预签名 URL 发 HEAD 返回 403 系 AWS SigV4 方法签名不匹配，**不计为访问缓解**。 | **中高** | 视点 A 已证（清理阻塞） |
| **KE-07** | `house.link.lianjia.com` | 核心 `saas_token` 缺失 `HttpOnly` / `Secure` / `SameSite` 全部属性；`shiro-cas` 换票链存在明文 HTTP 302 降级跳转。 | **中** | 视点 A 已证 |
| **KE-19** | `*.ttb.test.ke.com` | staging 解析至 RFC 6598 CGNAT 段（连续 6 个内网地址，原值已脱敏为 `<REDACTED_CGNAT_IPV4_*>`，见 `sidechannel-*-probe.json`），**视点 A 可达**且 `/openapi.json`（1,484 路径 / 1.8MB）与 `/docs` 匿名 200；办公网视点不可达，生产 `/docs` 与 `/openapi.json` 为 404。 | **中** | 视点 A 已证（限云内网视点） |
| **KE-23** | `app-moma.ke.com` | 链家 WebView 将 access+refresh token 写入 7 天 JS 可读 Cookie（`ke_sess_bak`，缺 `Secure/HttpOnly`）；弹窗登录经 `BroadcastChannel` 与 `localStorage` 分发两类令牌，未见定点 `removeItem`。 | **中** | 离线静态已证 |

### 2.3 P2 / P3、待证阻塞与低危汇总
| 编号 | 资产 | 摘要与当前结论 | 评级 / 状态 |
| --- | --- | --- | --- |
| **KE-08** | `app-prd` / `app-moma` | CAS 回调经 URL Fragment 传 `access_token` 并落 `localStorage`（存储位置已证，窃取路径未证）。**r7 补录（T6）**：生产 GoTrue 令牌交换终态回调主机残留本地开发默认值 `localhost:3000`（`round2-session-2026-11-23-read.json` 的 `app_token_exchange.terminal_redirect_host`，三跳链 CAS 回调后由 `app-prd` 落地），属生产环境 `SITE_URL` 配置缺陷；GoTrue `redirect_to` 白名单是否限制待下一授权窗口核查（T32）。 | **中**（P2） |
| **KE-10** | `img.ljcdn.com` | **a) 已证·中**：实勘/户型桶原图 403 被 `.450x.jpg` 等转码后缀 100% 绕过（与 `extranetAppear` 无关）；**b) 待确认**：封面桶原图直连公开。 | **中（仅 a）**（P2） |
| **KE-09** | `lease-pz` / `house.link` | CRM 写接口 CSRF：业务 Cookie 均无 `SameSite`，urllib 对照实证 `lease-pz` 仍反射 `evil.example` + `ACAC:true`；浏览器 harness 因列表引用覆盖与 SPA 覆盖 title 导致首轮取证无效（脚本已修复，待凭证恢复后复测）。 | **阻塞 / 待证**（P2） |
| **KE-26** | `prd-assistant-backend.ke.com` | 渲染（`html-to-pdf/image`）、自定义 MCP、Groovy 测试面：**匿名门均 401/403 有效**；登录态 `html-to-image` 404、`custom-mcp/test` 422、`test-groovy-filter` 字段名不匹配（`script 不能为空`，未进沙箱，但**普通员工未被管理 RBAC 403 拦截**）、OAST 未开。 | **阻塞 / 待证**（P1/P2） |
| **WD-01** | `wandou.ke.com` | 单账号合成 `streamId` 返回 `400 INVALID_PARAM` 且会话列表不跨主体（**单账号门防线有效**）；双账号 BOLA 与能力 URL 撤销/TTL 待测。 | **单账号有效 / 跨主体阻塞**（P2） |
| **KE-11 ~ KE-16** | `login` / `moma` / `wandou` / `ljcdn` | **低危 6 项**：CAS 接受固定 `deviceId/sign="default"`（KE-11）；Moma 未登录返回 HTTP 200+`errno=401`（KE-12）；豌豆 `negotiation/page` 稳定 502 HTML（KE-13）；`X-Wandou-Client-Id` 不强制（KE-14）；Moma 缺参报 500 及 `verify_token` 泄露 `<jwt>|<chat_id>` 格式约定，另 PostgREST/Postgres 内部码（`PGRST*`/`22P02`/`57014`/`42501`）直接回显（KE-15）；CDN 残缺后缀 `!m_fill,l_dy` 报 400 `InvalidCommand`（KE-16）。 | **低**（P3） |
| **KE-17 / KE-20** | 全域 | **KE-17（信息）**：Moma 前端 bundle 匿名暴露管理面路由清单（管理 RBAC 403 有效）；**KE-20（加固项）**：全站缺少 CSP/HSTS/XFO/nosniff 安全头及 `deploy_404` 占位路由暴露。 | **信息 / 加固项**（P2/P3） |

---

## 3. 核心高危发现与关键技术复核

### 3.1 文案 App 数据面失守（KE-21 / KE-27 / KE-28 & KE-25 / KE-29）
* **RLS 全面失守（匿名 + 普通用户）**：公开前端 bundle 内置 `role=anon` JWT，二期视点 B（匿名）与视点 A（普通用户 CAS 换取的 Supabase `access_token`）均实证：
  1. `GET /rest/v1/admins?select=id,username,password_hash` 返回 HTTP 200 非空列表（`admins` 实测 1 行），暴露 64 位 hex 单次无盐 SHA-256 管理员哈希（证据仅存长度与二次哈希，无原值）；
  2. `GET /rest/v1/users` 与 `/usage_logs` 返回员工 PII（`phone/ucid/email/org`）与使用日志；`GET /rest/v1/` 暴露 59KB 根 OpenAPI（9 表 14 RPC）。**实测爆炸半径（T3 精确计数，HEAD `count=exact`）**：`users` **80,795** 行、`usage_logs` **2,239,107** 行、`policy_news` 58,252 行、`new_buildings` 4,095 行、`admins` 1 行。`users.phone` 字段实测长度 **14**，非 KE-01 的 11 位裸手机号形态（可能含区号或掩码）；
  3. **RPC 超时双面函数名拆分（T5）**：用户态 `POST /rpc/get_usage_overview`（8.21s，HTTP 400 / `57014` statement timeout，证据 `round2-session-2026-11-23-read.json`）；匿名态 `POST /rpc/get_usage_overview_with_filter`（3.25s，同 `57014`，证据 `round2-anon-2026-11-23.json`）。同属匿名/低权限直达 DB 根因，证据引用分别落到函数名；
  4. **`prompts` 写权与 canary 清理存疑（T1 口径收紧）**：匿名（`KE21-write-prompts-insert-canary`）与普通用户（`KE21-user-insert-prompts-canary`）各取得一次 `POST 201`；随后按 canary 标记的 `DELETE`（均带 `Prefer: return=minimal`）两面都返回 `204`。但 PostgREST 在 `return=minimal` 下即使 RLS 静默拒绝（0 行受影响）也返回 `204`，因此**不再单凭 204 认定匿名或普通用户拥有 DELETE 权限**——该判定与本报告对 `admins` 零匹配 PATCH 204 的存疑标准一致。佐证：匿名写入 2 分钟后 `KE21-count-exact-prompts` 从 28 行（`0-27/28`）变为 `*/29`。生产 `prompts` 表可能残留**最多 2 条**合成 canary（匿名 + 用户各 1），补回读复查列入 T32 下一授权窗口，在回读确认前本表不写「清理闭环已完成」。
* **解密处理面无鉴权（KE-25 / KE-29，r7 按四场景精确拆分，T2）**：`POST /falcon/supabase-crypto/decrypt` 的实测行为对照三期 `r3-decrypt-crosschat-2026-11-24.json`（D1–D6）与二期 `followup-c`：
  1. `chat_id` 正确 + 匹配密钥 + 行内 `rows[0].id` 为零 UUID → **HTTP 200 / `code=0`**（不校验行归属）；
  2. `chat_id` 正确 + 匹配密钥 + 乱填 `table_name` → **HTTP 200 / `code=0`**（不校验表名白名单/归属）；
  3. `chat_id` 为零 UUID 或跨应用 + 带不匹配密钥（D2/D3/D4）→ **HTTP 400 / `code=1`**（`X-Envelope-Key 与 chat_id 不匹配`）——带密钥时绑定校验存在，并非旧稿笼统所述的一律 `code=0`；
  4. 彻底省略 `X-Envelope-Key` 头（D5 / `followup-c` no-key 用例）→ **HTTP 200 / `code=0`**，响应体从 `envelope` 密文信封退化为未封装的 `data` 数组——密钥头非必填，绑定校验可直接绕过，为最核心行为差异。
  信封密钥即 `btoa(utf8(app_id))`（前端公开函数，App ID 在所有发布物中明文可见），不构成保密边界；真实明文解出仍需业务方提供可销毁密文 fixture（沿用阻塞口径）。三期在第二个独立应用（应用 B，视频生成器）上原样复现全部行为。

### 3.2 算力盗刷、重放与并发熔断缺失（KE-02 / KE-03 / KE-22 / KE-30）
* 公开前端 6 处字面量使用同一 JWT 头部常量作为 HMAC 密钥，且目录/房源未签查询参数、AI/检索未签请求体。
* 运行时实证：① 同一 `X-Timestamp + X-Nonce + 签名` 连发两次均 200（**Nonce 不去重**）；② 篡改未签名 query 参数 200 放行；③ `ROUND2_BURST=1` 对 `/falcon/ai_endpoints/v1/chat/completions` 发起 5 路并发 `max_tokens=1` 请求（仅带应用级 HMAC，无个人凭证）全部返回 HTTP 200 真实补全信封（613B），无 429 或并发截断，证实**应用级 / 单出口 IP 并发与速率熔断缺失**。

### 3.3 视点 A CORS 双维度复核与一期冲突说明（KE-18 / KE-32）
视点 A 对 `prd-assistant-backend.ke.com/api/user/info` 与 `app-moma.ke.com/falcon/city/list` 执行 8 条探针（GET/OPTIONS × 匿名/带会话），结果如下：
* **CORS 反射维度（8/8 全部风险成立）**：8 条探针全部原样反射 `Origin: https://evil.example` 且 `Access-Control-Allow-Credentials: true`（归入 KE-18 治理范围）。
* **缓存投毒维度（5 条防线有效 / 3 条风险成立）**：
  * **5 条带 `Vary: Origin`（防线有效）**：`CORS-A-moma-backend-get-anon` 与 4 条 `OPTIONS` 预检。
  * **3 条 GET 缺 `Vary: Origin`（风险成立，KE-32）**：`CORS-A-moma-backend-get-session`、`CORS-A-app-moma-get-anon`、`CORS-A-app-moma-get-session`（`Vary` 仅为 `Accept-Encoding`）。
* **重点双重命中与冲突说明**：`CORS-A-moma-backend-get-session` 携带 `prd-assistant-token-prod` 返回 200 / `errno=0` 完整 `user_info`（540B）且**无 `Cache-Control: no-store`**，同时构成带凭证跨域读与缓存投毒条件。该观测与一期记录「Moma 后端带会话成功分支三轮均不反射」直接冲突——本报告以本轮视点 A 原始台账（`round2-session-2026-11-23-read.json`）为准记风险成立，一期「不反射」结论不再作为安全依据，待凭证恢复后做第三轮独立复现定位网关/灰度差异。

### 3.4 会话生命周期与登出吊销对照（KE-05 / KE-06 / KE-24）
阶段 4 在人工授权窗口对 `login.ke.com/logout` 执行真实登出，随后使用**登出前快照种入全新 `CookieJar` 独立重放**（排除响应清 Cookie 造成的假通过）：
1. **CAS `TGC` 族（KE-05，防线有效）**：登出前换票 302 签发 ST（票据已形状脱敏）；登出后独立重放旧 `TGC` / `TGC_Secure` / `security_ticket` 返回 200 CAS 登录页（1127B，无 `Location`、无 ST），服务端吊销有效。
2. **豌豆 Bearer（KE-24 豌豆侧，防线有效）**：登出后旧 `puzu_lease_token` 打 `/api/v1/users/me` 立即返回 `401 / U001`（带 `nosniff` 与 `no-store`）。
3. **Moma 会话 Cookie（KE-24 Moma 侧，风险成立）**：登出后独立重放旧 `prd-assistant-token-prod` 打 `/api/user/info` 仍返回 HTTP 200 / `errno=0` 完整 `user_info`，且响应体 SHA-256（`341ef410…54d9`）与登出前完全一致，CAS 登出未联动吊销 Moma 业务会话。

### 3.5 豌豆文件上传绕过与孤儿对象残留（WD-02）
* 登录态向 `/api/v2/note-files/upload` 上传 `.html`、`.svg`、`round2.php.jpg`、`../../round2.txt` 全部 HTTP 200 成功落盘对象存储，服务端无扩展名/路径穿越校验。
* 清理与路由穷举（`round2-cleanup-probe-2026-11-23.json`）：仅双扩展名 `.php.jpg` 可经 `create-image` 绑定并由 `notes/delete` 删净；其余 3 类被 `create-image` 以 400 拒绝。穷举 `DELETE /note-files/{id}`、`POST /note-files/delete`、`POST /note-files/remove` 均返回 404 `NOT_FOUND`，结合前端契约确认**平台无用户可达的孤儿文件删除接口**。当前账号笔记列表为空，仅对象存储残留 4 个无害合成 canary 对象（3 个 write 轮 + 1 个 cleanup 探针），待对象存储生命周期策略或运维侧按 `fileKey` 清理。
* **403 归因纠偏**：`ods.lianjia.com` 能力 URL 返回 403 `application/xml` 是因为对签发给 `GET` 的 AWS SigV4 预签名 URL 发送了 `HEAD`（方法签名不匹配），不能据此认定对象不可读或存在访问缓解。

### 3.6 三期双应用核实：脚手架系统性与同源隔离（P0-3 / P1-4）
三期以两个独立构建、独立租户的 Lite App（应用 A 文案生成器、应用 B 视频生成器 `/shipinshengcheng`）做匿名面交叉核实（70 请求，0 个人凭证）：
* **P0-3 平台脚手架系统性缺陷（风险成立）**：公开 HMAC 密钥常量（剔除 anon JWT 前缀后，作为 HMAC secret 的独立常量 A 应用 6 处、B 应用 4 处）、宽 RLS、`ke_sess_bak` 7 天 Cookie、`supabase-crypto/decrypt` 代理、无 CSP/DOMPurify 在两个应用同构复现；调试/管理面路由（`admin_session`/`password-debug`）仅出现在带管理台的应用 A，属应用个案。应用 B 池 anon key 实测可读 `smart_clip_jobs`/`properties`/`smart_clip_music` 非空业务行（KE-21/27/28 跨应用复现），两张池 schema 独立（A 池 9 表 14 RPC，B 池 14 表 8 RPC，无交集）。
* **匿名门有效项（防误报）**：`/falcon/aroute/*` 五接口仅持公开应用签名时 12/12 全部 401，KE-01 维持「登录员工越权」原评级，不升级为匿名暴露；预览路由生产宿主 302→`deploy_404`，4 个 Expert 管理 API 匿名 `errno=401`。
* **阻塞项（不并入通过）**：网关是否误接受 `.ke.com` 主站 Cookie-only 身份（本轮全程无个人 Cookie，401 仅证匿名门有效）、计费类 AI 端点的 `X-App-Id` 配额归属（仅证公开字典路由 `city/list` 不校验 App ID）、带凭证跨域读成功业务响应（4 条 CORS 探针全部命中错误分支，错误响应仍无条件反射任意 Origin + `ACAC:true`，归 KE-18 治理面）。
* **同源多应用横向隔离（T16/T17）**：多租户/多作者 Lite App 均托管于同一源 `https://app-moma.ke.com`，共享同源 `localStorage`（`<appId>.supabase.auth.token`、`admin_session`）与 `path=/` 的 `ke_sess_bak`——任一 Lite App 存在 XSS 或恶意前端代码即可读取同域下全部 Lite App 令牌，无需突破 SOP。**结构性修复（T17）**：单应用独立子域（如 `<appId>.app-moma.ke.com`）或强制 `sandbox` iframe 隔离；单纯添加 CSP 无法根治同源 UGC 互访，已写入 §6 路线图。

### 3.7 四期离线深挖：R4-S01~S04 新增发现与阻塞假设（0 实时请求）
四期全部为离线静态分析、历史台账量化复核与 dry-run 探针计划（实时请求数 = 0；4 份 dry-run 计划不计运行时实证）。新增条目按三层 taxonomy 列报，不另立 `KE-*` 矩阵行：
* **R4-S01（中·架构线索）**：管理面存在 `falcon-gateway-acl` 的 audit/enforce 双态开关（Redis `falcon:acl:*`）与多项灰度/白名单配置（`security_gray`、`antispider-gray`、`customer-sms-whitelist` 等）；静态面无法观测生产处于 audit 还是 enforce，结合 M4 五路并发全 200 的强实证，规则可能仅灰度未全量、只记不拦或作用域不含 `/falcon/*`。
* **R4-S02（中·架构事实）**：moma-sdk 的限流（`rateLimitEnabled` 等）与 HMAC 签名在 XHR/fetch 包装层本地执行（本地 Map 计数，`Request blocked` 仅发生在浏览器内），任意非浏览器 HTTP 客户端可整体绕过，不能计入服务端防护纵深。
* **R4-S03（中·架构线索）**：访客短信登录存在手机号 Redis 白名单（`customer-sms-whitelist`），暗示默认拒绝、灰度放行；白名单全量放开前若频控未同步切全量即形成完整爆破面（默认手机号拒绝语义待授权窗口验证）。
* **R4-S04（中·静态线索）**：个人 `inviteCode` 是用户长期属性并用于公开邀请链接（`/invite/${inviteCode}`），与一次性邀请语义混用；防护须依赖高熵生成 + `/api/invite/accept` 端限流 + 服务端租户归属校验。
* **阻塞假设 R4-B01~B07**：短信爆破五要素（挂靠 M5/`ROUND2_SMS`）、GoTrue CAPTCHA 可选、分享 Token 熵/TTL、accept 限流、CAS 撞库防护四要素（KE-11 提级候选）、房源数字 ID 遍历量化（挂靠 KE-02/03）、豌豆跨账号 BOLA（挂靠 WD-01），全部待下一授权窗口按 `scripts/security/round4/r4_*.py` dry-run 计划实跑收口。
* **口径修正**：四期可解析台账子集 255 条（非全证据库请求量），其 0/429 仅为弱旁证；限流/熔断缺失的强证据仍以 M4 五路并发突发（KE-30）与 KE-01 翻页为准。

### 3.8 五期授权窗口补盲（round5，2026-11-24，非破坏 100 请求：匿名 55 + 登录态 45；登出专项 13 请求另见 §3.9）
本窗口在凭证恢复后执行：匿名面（视点 B 工作站，公开 anon JWT，55 请求）与登录态面（视点 A `beike-server`，单授权员工会话，45 请求），覆盖 tasklist T1、T10–T15、T18–T21、T25–T26。探针为 `scripts/security/round5/r5_anon_*.py` 与 `r5_session_*.py`，证据 4 份在 `evidence/round5/`（登出专项 2 份见 §3.9）。

**KE-21 纵深（Supabase 两池）**
* **T1 canary 残留坐实（风险确认）**：对 `prompts` 按 canary 标记 `name=eq.__round2_canary_20261123__` 精确回读，`Prefer: count=exact` 头为 `0-1/2`、id-only GET 数组长度 **2**——生产表确有 **2 条**合成 canary 残留（匿名 + 用户各 1），证实 r7 存疑：两次 `return=minimal` 的 `DELETE 204` 均被 RLS 静默拒绝（0 行受影响仍回 204）。建议运维按该 name 标记清理，并确认 `prompts` 的 DELETE 策略。
* **T10 `schema_migrations` 匿名可读（风险成立）**：应用 A 池 `GET /rest/v1/schema_migrations?select=version,sql` 匿名 **HTTP 200**（`select=*` 响应 1,888B，含 `sql` 列 1,809B），DDL/迁移历史对匿名角色开放，可反推建表语句、RLS 策略与 RPC 定义。
* **T13 RPC 匿名执行面（严重，KE-21 扩面）**：应用 A 池 14 个 RPC 中 **12 个匿名可执行**——6 个直接 200（含写语义 `sync_usage_logs_city` 200、`get_hourly_trend*`、`get_top_templates*`、`get_top_users_with_filter`），另 6 个返回 `57014` statement timeout（**已通过鉴权执行到 DB，仅查询超时，同样计为可达**：`get_usage_overview*`、`get_available_cities`、`get_city_statistics*`、`get_top_users`）；仅 `get_daily_trend*` 2 个因必填参数缺失返 PGRST202（参数化可达性待定，非权限拦截）。应用 B 池 **8/8 全部匿名 200**，含敏感聚合 `get_active_user_emails`（响应 **37,424B**）、`get_user_usage_details`（≥65,536B，触读取上限）、`get_visit_stats` 等；`_app_current_user_role_ids`/`_app_ehr_current_subordinates` 匿名返空（角色函数无用户上下文）。**双视点对照（权限可达集合一致，超时阈值不同）**：登录员工态同为 12/14 可达，但构成是 **11×200 + 1×57014**——匿名态超时的 6 个 RPC 中有 5 个（`get_available_cities`、`get_city_statistics*`、`get_top_users`、`get_usage_overview_with_filter`）在员工态直接 200 返回数据，仅 `get_usage_overview` 仍 8.21s 超时（匿名态该函数 3.25s 超时）。这与 PostgREST/Supabase 按角色配置的 statement_timeout 差异吻合（anon 约 3s、authenticated 约 8s），说明两层均无有效 EXECUTE 收权，差异仅在超时预算而非授权边界。
* **T15 业务表匿名/员工写权（严重）**：空对象 `INSERT {}` 按 Postgres 报错分层判定——RLS 拒绝（`42501`）仅 `users`、`admins` 两表（匿名与员工均被拒）；**穿过 RLS、仅被 NOT NULL 约束拦截（`23502`，证明持 INSERT 权、补齐合法列即可写行）的表**：应用 A `usage_logs`、`prompts`、`policy_news`，应用 B `properties`、`smart_clip_jobs`，匿名与登录态结论相同。
* **T14 匿名自主注册未关闭（风险成立）**：两池 `GET /auth/v1/settings` 均 **`disable_signup=false`**，列出 email/phone 及 30 个外部身份提供商（含 wechat/feishu/dingtalk/wecom 等）。外部攻击者可自助注册换取 `authenticated` JWT 的开关未禁用；为避免产生无法自助清理的账号，未实际提交 signup（settings 为权威非破坏证据），实际注册是否需 CAPTCHA/审核留待授权窗口单点验证。
* **T11 Storage（部分成立）**：两池 `GET /storage/v1/bucket` 均 **HTTP 200**（非 401/403，数据面未在边缘拦截），但匿名可见桶数为 0（RLS 过滤为空或确无桶），桶内对象列举无桶可测；控制面 Moma 后端 `/api/internal/supabase` 见 T19。
* **T12 Edge Functions（匿名面防线有效）**：两池 `GET /functions/v1/` 均 **401**、合成函数名 404，匿名调用在网关层被拦；真实函数名无法枚举，登录态函数鉴权仍待有函数清单后补测。

**Moma 后端高危端点员工 RBAC（T18–T21，视点 A 有效会话，正对照 `/api/user/info` errno=0）**
* 匿名基线：**抽样的 6 条路径**（`/api/internal/auth`、`/api/internal/platform-keys`、`/api/admin/sudo`、`/api/wechat/access-token`、`/api/platform-api/iam-credential`、`/api/load-test/run`）无会话时统一返 HTTP 200 + `errno=401`（框架级登录门存在，64B 信封）；其余 8 条员工态路径未做匿名对照，不外推。
* **登录员工态穿透（无管理 RBAC 403，仅过登录门）**：① **T21 `/api/wechat/access-token` 返 200/errno=0，`data.access_token`（137 字符）+ `expires_at`**——普通员工可直接领取微信访问凭据（证据仅存键名与长度，无原值）；② T21 `/api/platform-api/iam-credential` 返 422（穿过鉴权到参数校验，补齐参数可达）；③ T20 `/api/load-test/run` GET 405（路由存在）、空体 POST 422（过鉴权，DoS 放大器对普通员工可达，未用合法参数触发真实压测）；④ T20 `/api/chats/proxy` 返 200/errno=1「聊天不存在」（业务可达，SSRF 代理面，缺有效 chat_id 未深入）。
* **404 存疑（不作「已屏蔽」结论）**：`/api/internal/{platform-keys,supabase,security-storage,auth}`、`/api/admin/sudo`、`/api/messages/_debug`、`/api/moma-data/{hive,cli}`、`/api/cache/redis`、`/api/offline/tasks` 在员工态均 404——可能未部署于该后端或经网关摘除路由，与匿名 401 形态不同，需平台白盒确认是否在其他宿主/网关暴露。

**前端供应链（离线证据，T25–T26）**
* **T25 SRI 全缺**：应用壳引入的全部 CDN 脚本（含 `ai-security-sdk.umd.js`、`keRiskControl.js`、`dig-tracker`、`moma-sdk-prod`）0 个 `integrity`/`crossorigin` 属性，CDN 被投毒即无完整性拦截。
* **T26 长缓存滞留**：`/shipinshengcheng` HTML 壳 `Cache-Control: public, max-age=2592000`（30 天）；对照 `expert-shell` 与 `app-moma` 根均为 `no-store`。应急轮换密钥或修复前端后，旧 HTML 壳会在客户端/中间缓存滞留至多 30 天。

### 3.9 五期登出吊销专项（T22/T23，破坏性；round5 台账 13 请求 = revoke 8 + confirm 5）
本专项在独立授权窗口执行，目标方法为「登出前正对照 → 登出 → 全新 CookieJar 重放登出前快照令牌」，杜绝 Set-Cookie 清 Cookie 造成的假通过；执行后服务端会话已销毁，需重新扫码。**取证发生一次台账覆盖事故，结论按可溯源证据重分级如下。**

* **T22 Moma 自有登出服务端吊销——阻塞（round5 台账丢失，待重新扫码补采）**：探针 v1 运行（15 请求）曾观测到 `POST /api/user/logout` 200 后旧 `prd-assistant-token-prod` 全新 jar 重放仍 200/errno=0，但 v1 写入的台账被随后的 v2 运行**同路径覆盖**，工作站与服务器均无备份、无法恢复。归档的 `r5-logout-revoke-2026-11-24.json` 是 v2（8 请求），其 `session_established` 为 `{supabase_jwt:false, moma:false}`、`T22_moma_logout` 为空对象——v2 启动时 TGC 已失效，不含任何有效的 T22 配对证据（该文件 meta 已加 `data_quality_note` 说明）。注意 T22 命题（Moma **自有登出端点**是否吊销）与 KE-24（CAS 登出联动）不同，不能用二期证据替代，故改判**阻塞**，重新扫码后按 v2 修复版脚本补采。
* **T23 CAS `/logout` 多子系统联动——结论沿用二期完整配对证据，round5 仅作登出后状态快照**：
  * **CAS `TGC`（防线有效，二期证据）**：二期 `round2-session-2026-11-23-logout.json` 有完整配对——登出前 `M2-tgc-exchange-pre-logout` 302（正常签发 ST）→ `M2-cas-logout` 200/1,134B → 登出后 `M2-tgc-exchange-post-logout` 200/1,127B 无 ST（仅 CAS 登录页），服务端 TGC 吊销成立。round5 `r5-logout-confirm` 补采到同形态（换票 200/1,127B 无 ST、重新下发登录态 cookie 名），但因无同文件登出前正对照，仅作辅助印证。
  * **豌豆 `puzu_lease_token`（防线有效，二期证据）**：二期配对 `M2-wandou-me-pre-logout` 200/biz=0 → `KE24-wandou-me-old-token` 401/`U001`，CAS 登出后豌豆令牌被吊销。
  * **Moma `prd-assistant-token-prod`（风险成立，KE-24，二期证据）**：二期配对 `M2-moma-userinfo-pre-logout` 200 → `KE24-moma-userinfo-old-token` 仍 200/540B，CAS 登出不联动吊销 Moma 业务会话。
  * **Supabase JWT / `saas_token` / `lianjia_trusteeship_token`（阻塞，待重新扫码）**：round5 未取得三者「同端点登出前有效正对照 + 登出后重放」的干净配对。登出后只读快照（`r5-logout-confirm`）显示：买卖域 `house.link.lianjia.com/` 302 跳 `login.ke.com/login`（当前无有效会话，但无法隔离 CAS 单点登出与令牌自然过期）；`/shiro-cas` 无票 302 跳 `/error/500`；托管域 `trusteeship.link.lianjia.com/house/detail/` 带 cookie 与匿名均返回**逐字节相同的 200 公开壳**（该端点不构成鉴权门，不能据此判定）。三项须在重新扫码后用各域**真实鉴权 API**（非公开壳）做正对照+重放补测。
* **方法学勘误（已修正并留痕，脚本复跑不受影响）**：① 首次 CAS 登出误用 `Accept: application/json` 触发 406 内容协商、登出未真正执行，改浏览器 Accept 后才返回 200；② `dict(HTTPMessage)` 在存在重复 `Set-Cookie` 时退化为大小写敏感导致 Location 捕获为空，已改为直接在 HTTPMessage 上取值并以只读 `r5-logout-confirm` 重新采证；③ v1 台账被 v2 同路径覆盖且无备份，是本次证据断层的直接原因，后续破坏性探针改为带时间戳/序号的文件名，禁止同路径覆盖。

---

## 4. 已验证有效防线（防误报清单）

以下检查项经运行时或静态实证防线有效，不计入漏洞：
* **认证与登出防线**：CRM 租赁/买卖与豌豆匿名门有效；CAS ST 一次性消费有效；CAS `/logout` 服务端吊销旧 `TGC` 有效（独立重放实证）；豌豆旧 Bearer 登出后立即 `401 U001` 有效；Moma 管理面 RBAC（`/api/admin/users/search` 等返回 403）有效。
* **旁路与隔离防线**：CAS `service` 白名单、`gotoURL` 开放重定向、Authing `redirect_uri`、Moma 跨环境会话隔离 4 条旁路实测关闭；`house.link` 与 `wandou` 带会话不反射 CORS；`WD-01` 单账号合成 `streamId` 返回 `400 INVALID_PARAM` 且会话列表不跨主体；豌豆文本笔记创建→回读→删除闭环正常。
* **签名、RLS 与输入防线**：HMAC 缺失、降级占位串（`MOMA-NO-SIGN-WARNING`）与过期时间戳三类负对照均 401 拒绝；`admins` 表匿名 INSERT 被 RLS `42501` 拒绝；PostgREST 排序/过滤注入语法均 400 拦截；搜索建议 SSTI/引号零反射；渲染/MCP/Groovy/上传/SSE 匿名门均 401/403/U001。
* **客户端静态防线**：文案 App Markdown 使用 `skipHtml:true`；主站 `uo()` 与文案 App `postMessage` 均做精确 `origin` 校验（静态已证）；应用层无 WebSocket 握手面（CSWSH 静态关闭）。
* **三期新增有效项（匿名面）**：`/falcon/aroute/*` 五接口仅持公开应用签名 12/12 全部 401（个人 Bearer 门独立存在，KE-01 不升级为匿名暴露）；跨应用 anon JWT 池交叉 401（`WWW-Authenticate: Key`），两池 schema 独立、互查表返回 `PGRST205`；friday 预览生产宿主 302→`deploy_404`，4 个 Expert 管理 API 匿名 `errno=401`；staging 管理面 POST 门 8/8 `errno=401`；前端 UUID/随机 ID 生成走 `crypto.randomUUID/getRandomValues`（GoTrue polyfill 标准 fallback，未见 `Math.random` 直出安全 Token）。
* **已触达不评定主机**：`wxwork.ke.com`（404 统一 JSON）、`smart-mo.ke.com`（200 SPA 壳）、`moma-data.ke.com`、`flow-forge.ke.com`、`linkspace.ke.com`（视点 B TLS EOF）、飞书连接器（匿名 `errno=401` 门有效，视点 B 反射 Origin 但带 `Vary: Origin`），仅做基线触达，不单独评级。

---

## 5. 当前阻塞项与复测清单（不并入通过）

> 下列项均按红线或证据完整性标为 **阻塞**；待人工重新扫码恢复 `credential_store.bin` 及获得相应窗口后，使用已修复脚本逐项闭环。

| 分类 | 阻塞项 | 阻塞根因与解锁条件 |
| --- | --- | --- |
| **脚本已修复，待恢复凭证后复测** | **KE-09（跨站/同站 CSRF）** | 原 harness 共享可变列表 `responses` 并 `clear()` 致跨站观测被同站覆盖，且同站 `title` 被 SPA 覆盖；已修复为独立列表 + `fetch_result` 结构化返回 + 请求方法/发起方记录。 |
| **需补 Moma 会话** | **`postMessage` 运行时负对照** | harness 未注入 `moma.ke.com` Cookie，沉降到 `login.ke.com`，未触达主站 `uo()`；已新增 `target_origin_matched` 防误判，需注入 Moma 会话复测（当前仅静态 `r2-05/06` 有效）。 |
| **契约/方法待对齐** | **KE-26（渲染 404 / MCP 422 / Groovy 字段错）** | 登录态 `html-to-image` 404、`custom-mcp/test` 422、`test-groovy-filter` 发送 `expression`/`filter` 与服务端 `script` 字段不匹配未进沙箱（且普通员工未被 403 拦截）；需对齐真实契约复测。 |
| **契约/方法待对齐** | **M3 批量赋值（`/api/v3/aschat/config`）** | 脚本误发 `POST`（契约 `PUT`）命中通用 500 HTML 错误页（3006B，SHA `0730a5f9…`），缺合法字段写入 200 正对照；需改 `PUT` 并先做合法写入正对照。 |
| **环境/窗口外部依赖** | **KE-26 OAST 外联 / 短信 M5 / 存储型 XSS 投递 / 跨小时 TTL** | 需受控 canary 域（`ROUND2_OAST`）、单独授权手机号（`ROUND2_SMS`）、可销毁租户投递验证（`ROUND2_XSS_WRITE`）、65 分钟双定点校验 Supabase Token 严格过期与 Refresh 轮换。 |
| **运维清理与红线搁置** | **4 个上传孤儿对象 / 双账号 BOLA / 蓝队 SIEM 告警** | 4 个合成上传 canary 待对象存储 GC/运维清理；全部跨账号 BOLA/IDOR（含 `KE-01`、`WD-01`、跨店/跨城）需第二授权账号解锁；告警回溯需蓝队 SIEM 权限。 |
| **三/四期新增（r7 并入）→ round5 收口进展（r8）** | **Supabase 纵深 / Moma RBAC / 会话闭环 / 前端供应链** | **r8 已闭环（见 §3.8/§3.9）**：T1（残留 2 条坐实）、T10（schema_migrations 匿名可读）、T11 数据面（0 桶可见）、T12 数据面（Functions 匿名 401）、T13（RPC 匿名执行面 A 12/14、B 8/8）、T14（disable_signup=false）、T15（5 表持 INSERT 权）、T18–T21（员工态穿透 4 端点，新增 KE-33，+ 404 存疑清单）、T25（SRI 全缺）、T26（30 天长缓存）。**仍阻塞**：① **T22**（Moma 自有登出服务端吊销——v1 台账被 v2 同路径覆盖且无备份，round5 无有效配对证据，待重新扫码补采）；② T23 残余（Supabase JWT/saas/trusteeship 登出联动；TGC/豌豆/Moma 三项以二期 logout 台账为准，需重新扫码后用各域真实鉴权 API 做正对照+重放）、T24 遗留 Cookie 信任面；③ T11/T12 控制面（Moma 后端 `/api/supabase/*` RBAC，关联 404 白盒）、登录态 Functions 真实函数鉴权；④ R4-B01~B07 七项 dry-run 实跑（短信/分享 Token/CAS 撞库/限流矩阵，需授权手机号与窗口）；⑤ T27/T28 AI Agent 间接提示词注入与 Skill 沙箱（需受控 canary 域）；⑥ 双账号 BOLA、存储桶对象级权限（需第二账号/桶清单）。 |

---

## 6. 修复优先级路线图

| 阶段 | 时间目标 | 整改重点 | 对应编号 |
| --- | --- | --- | --- |
| **P0（紧急止血）** | 24–72 小时 | 1. 锁定 Supabase 全表 RLS 白名单、关闭根 OpenAPI 匿名访问、撤销 `anon`/普通用户对 `admins`/`users`/`usage_logs`/`prompts` 的非法读写，按哈希泄露预案轮换管理员口令并迁移至服务端会话；<br>2. `/supabase-crypto/decrypt` 强制个人鉴权与表/行归属校验，密钥服务端化；<br>3. Moma 员工目录实施字段白名单、组织范围限制与速率熔断；<br>4. 文案 App AI/房源接口强制个人会话、配额与并发熔断；HMAC 增设 Nonce 去重并覆盖完整 query/body，降级为风控信号；<br>5. `lease-pz`/`bms.lease`/`trusteeship`/`prd-assistant-backend`/`app-moma` CORS 全部改为显式白名单、禁 `null` Origin、补 `Vary: Origin` 与 `Cache-Control: no-store`。 | KE-21, KE-27, KE-28, KE-25, KE-29, KE-01, KE-02, KE-03, KE-22, KE-30, KE-18, KE-32 |
| **P1（高优加固）** | 1–2 周 | 1. CAS `/logout` 联动吊销 Moma `prd-assistant-token-prod` 会话，缩短服务端 TTL 并补 `Secure`；<br>2. 豌豆网关严格校验 `aud=wandou`（拒绝普租与托管票据），淘汰 `localStorage` 存储；<br>3. CAS `TGC` 设绝对有效期与异地/设备风控告警；<br>4. Moma 主站 `MessageCard`/`MessageCenterDrawer` 引入 DOMPurify 或改 `textContent` 并补 CSP；<br>5. 豌豆上传强制扩展名/魔数白名单与文件名净化，启用未绑定孤儿对象自动过期 GC 并补删除接口；<br>6. 修补 `saas_token` 属性与 `shiro-cas` HTTP 降级；清除 WebView 7 天 `ke_sess_bak`；收敛 staging 云内网访问并关闭其 `/docs`/`/openapi.json`；复核 `/platform-api/test-groovy-filter` 等管理接口 RBAC。 | KE-24, KE-04, KE-05/06, KE-31, WD-02, KE-07, KE-23, KE-19, KE-26 |
| **P2 / P3（纵深防御与收敛）** | 2–4 周及持续 | 文案 App 升级 Auth Code + PKCE；CDN 实勘/户型转码继承源桶鉴权；复测并收口 KE-09 CSRF、KE-26、WD-01；补齐全站安全响应头基线；统一错误信封收敛内部错误码、401/500/502 语义与脏数据。**同源 UGC Lite App 结构性隔离（T17）**：多租户 Lite App 收敛为单应用独立子域（如 `<appId>.app-moma.ke.com`，按子域切分 Cookie 作用域与同源存储），或对用户生成前端强制 `sandbox` iframe 隔离（禁同源、禁顶窗导航）；**明确：单纯添加 CSP 无法根治同源 UGC 互访**——同源下各应用共享 `localStorage`（`<appId>.supabase.auth.token`、`admin_session`）与 `path=/` 的 `ke_sess_bak`，任一应用 XSS/恶意代码即可读取同域全部令牌，CSP 只能降低注入概率，不改变同源可读事实。配套核查 GoTrue `SITE_URL`（修复生产残留 `localhost:3000` 回调，T6）与 `redirect_to` 白名单。 | KE-08, KE-09, KE-10a, KE-11~17, KE-20, WD-01, T16/T17 |

> **范围外独立备忘（不计入 `KE-*` 序列）**：二期本地回归发现工作站活动配置 `.mcp.json` 中一处自建核验服务使用明文 HTTP 地址，已剥离出本 `ke.com` 审查序列，另按自建基础设施加固项独立跟进。

### 6.1 round5（五期）新增整改项（按 §3.8 实证）
* **P0（24–72 小时，Supabase 两池）**：① 回收 `anon`/`authenticated` 对全部业务 RPC 的 `EXECUTE`，按白名单逐个重授（A 池 12/14、B 池 8/8 现匿名可调，含写 RPC `sync_usage_logs_city` 与 PII 聚合 `get_active_user_emails`/`get_user_usage_details`），`SECURITY DEFINER` 函数内部补 `auth.uid()` 归属校验；② 对 `usage_logs`/`prompts`/`policy_news`（A）、`properties`/`smart_clip_jobs`（B）补 INSERT RLS 策略（现仅 `users`/`admins` 拒绝）；③ `schema_migrations` 撤销 anon/authenticated 读权（含 `sql` 列）；④ GoTrue 关闭外部自主注册（两池 `disable_signup=false`），收敛 email/phone/外部 IdP；⑤ 清理 `prompts` 表 2 条残留 canary（按 `name=__round2_canary_20261123__`）并修复 DELETE 策略。
* **P1（1–2 周，Moma 后端 RBAC，KE-33）**：⑥ `/api/wechat/access-token`、`/api/platform-api/iam-credential` 增加管理 RBAC 并审计凭据领取留痕（普通员工现可过登录门直达，KE-33）；⑦ `/api/load-test/run`、`/api/chats/proxy` 纳入管理 RBAC + 网关白名单（DoS 放大器/SSRF 面）；⑧ 平台白盒确认 `/api/internal/*`、`/api/admin/sudo` 等 404 路径在其他宿主/网关的暴露面与 `sudo_active`/`real_ucid` 审计。
* **P2（前端供应链）**：⑨ 全部 CDN 脚本补 SRI（`integrity`+`crossorigin`），风控/安全 SDK 优先自托管；⑩ `/shipinshengcheng` HTML 壳取消 30 天 `max-age`，改为 `no-store` 或带构建指纹的短缓存。
* **待重新扫码/专项授权**：T22（Moma 自有登出服务端吊销，round5 台账覆盖丢失后阻塞）、T23 残余（Supabase JWT/`saas_token`/`lianjia_trusteeship_token` 登出联动，需各域真实鉴权 API 正对照+重放）、T24 遗留 Cookie 信任面、T27/T28 AI Agent 间接提示词注入与 Skill 沙箱评估（需受控 canary 域与工具链授权）。

---

## 7. 关键技术附录与全量证据索引

### 7.1 关键 Cookie 与令牌属性核实表（仅属性，无原值）
| 凭证 / Cookie | 形态 / 长度 | Secure | HttpOnly | SameSite | 关键结论 |
| --- | --- | --- | --- | --- | --- |
| `TGC` / `TGC_Secure` | 不透明 86B（非 JWT） | 是 | 是 | 未声明 | 全域通兑、无设备/IP 绑定（KE-05/06）；`/logout` 服务端吊销已证有效 |
| `prd-assistant-token-prod`（重签发） | Moma 会话（`Expires=2083`） | **未声明** | 是 | Lax | 删除事件（`Max-Age=0`）与重签发分列；**CAS 登出后重放仍 200（KE-24）** |
| `puzu_lease_token(_secure)` | 不透明 44B（非 JWT） | 是 | 是 | **未声明** | 跨应用被豌豆接受（KE-04）；CAS 登出后豌豆侧立即 401（防线有效） |
| `lianjia_trusteeship_token` | 不透明 44B（同构） | 是 | 是 | 未声明 | 同被豌豆网关接受并可只读客源（KE-04 扩面） |
| `saas_token` | 不透明 32B hex | **未声明** | **未声明** | **未声明** | 买卖域核心必需 Cookie，属性全缺（KE-07） |
| `ke_sess_bak` | WebView JS Cookie（7 天） | **未声明** | **否** | Lax | 含 access + refresh token JSON（KE-23） |
| `lianjia_ssid` / `lianjia_uuid` | 会话 / 10 年标识 | **未声明** | 未声明 | 未声明 | 已勘误早期转录错误，全量样本均未声明 `Secure`；三期匿名面复证下发于 `.ke.com; path=/`，无任何安全属性 |
| `_ucid_` / `_account_` / `_usercode_` / `_displayName_`（r7 补录，T24） | 遗留明文身份 Cookie（证据 `ke-authz-followup-2026-11-23.json`） | **未声明** | **否** | Lax | 四个均为客户端可篡改的明文身份标识；是否存在老接口信任这些 Cookie 做身份判定待授权窗口排查（T32） |

### 7.2 全量脱敏证据索引（r8 锁定：共 56 份 JSON/TXT = 21 + 16 + 7 + 6 + 6；另由统一门禁产出 `redaction-gate-selfcheck.json`）

#### A. 一期与最小轮证据（`evidence/`，21 份）
| 文件 | 内容与口径 |
| --- | --- |
| `property-auth-audit-evidence-2026-11-20.json` | 20 条 primary 认证对照 + 5 `sale_refreshed` + 2 `fresh_public_proof` + 1 `invalid_signature`（共 28 条） |
| `injection-matrix.json`、`injection-probe.json` | 令牌/凭证注入矩阵（非输入注入） |
| `idor-probe.json`、`cross-account-probe.json` | 单主体参数注入 23 块 + 跨账号形态 7 组 8 键 |
| `moma-directory-*.json`（5 份） | 员工目录边界/确认/抽样/去重/枚举路径（仅聚合统计） |
| `sidechannel-anon/authed/focus-probe.json`（3 份） | 旁路三轮（CORS PoC、CAS service/gotoURL/Authing 负对照） |
| `staging-map.json`、`staging-critical.json` | staging OpenAPI 1,484 路径测绘与高危端点鉴权门 |
| `review-fix-facts.json`、`token-form.json`、`wandou-param-shapes.txt` | Cookie 属性矩阵、带会话 CORS、令牌形态解析、豌豆参数契约 |
| `minimal-round-2026-11-23.json`、`minimal-post-correction-2026-11-23.json`、`ke-authz-followup-2026-11-23.json` | 2026-11-23 单账号最小轮（37 请求）+ POST 修正（3 请求）+ 同日独立跟进矩阵 |

#### B. 二期匿名/静态/会话窗口证据（`evidence/round2/`，r7 起 16 份 JSON）
| 文件 | 内容与请求数 |
| --- | --- |
| `round2-anon-2026-11-23.json`、`round2-anon-followup-{a,b,c}-2026-11-23.json`、`round2-reflection-bool-2026-11-23.json` | 视点 B 匿名/应用签名全套（55 + 17 + 11 + 7 + 5 = **95 请求**）：RLS 取证、canary 写探针（清理闭环待回读，见 3.1）、解密 Oracle、HMAC 重放/篡改、注入负对照、反射探针。r7 起 followup-a/c 低熵字段（username/city/user_name/phone/email 等）只保留类型与长度，无盐 `sha16` 已清理；UUID 与 `password_hash` 二次哈希保留 |
| `round2-static-verification-2026-11-23.json`、`round2-dom-sink-proof-2026-11-23.json` | 离线静态 28/28 通过、本机 Chrome CDP `file://` 水槽实证。原 `round2-local-hardening-2026-11-23.json`（工作站 IP/端口/PID/本机路径，范围外）已移出 evidence，落 gitignore 的 `outputs/security-local-workproduct/` |
| `round2-session-2026-11-23-read.json` | 视点 A 阶段 1 只读/幂等：用户面 RLS、KE-25/26、WD-01、视点 A CORS 8 探针、KE-09 urllib 对照（**26 请求**） |
| `round2-session-2026-11-23-write.json` | 视点 A 阶段 3 受控写：M3 批量赋值、四类上传与能力 URL HEAD、绑定/清理、笔记闭环（**21 请求**） |
| `round2-session-2026-11-23-burst.json`、`round2-session-2026-11-23-sms.json` | 视点 A 阶段 3 M4 并发熔断（**6 请求**）、M5 短信频控（**0 请求，skipped**） |
| `round2-session-2026-11-23-logout.json` | 视点 A 阶段 4 破坏性登出与独立重放（**7 请求**；ST 已形状脱敏） |
| `round2-browser-harness-2026-11-23-csrf.json`、`round2-browser-harness-2026-11-23-csrf-rerun2.json` | 视点 A 阶段 2 浏览器 harness 首轮与复测（取证污染/沉降错位，留存溯源，不作通过依据） |
| `round2-cleanup-probe-2026-11-23.json` | write 轮残留处置与删除路由穷举（**11 请求**；`fileId` 已 `hide_path` 脱敏） |
| `round2-redaction-selfcheck.json` | 二期当轮脱敏自检记录（历史产物；r7 起门禁统一收口到根目录 `redaction-gate-selfcheck.json`） |

#### C. 三期双应用核实证据（`evidence/round3/`，7 份 JSON，70 请求）
| 文件 | 内容与请求数 |
| --- | --- |
| `r3-surface-fetch-2026-11-24.json` | 应用 B 外壳/bundle/Expert 外壳抓取、JWT 形状与安全头（10 请求） |
| `r3-runtime-probe-2026-11-24.json` | aroute 匿名门、池密钥交叉、预览/Expert、CORS、配额主台账（39 请求） |
| `r3-decrypt-crosschat-2026-11-24.json` | 解密跨 chat_id 六用例 D1–D6 + B 池 schema 元数据 + B 池 anon RLS（13 请求） |
| `r3-expert-gate-2026-11-24.json` | Expert 管理 API 匿名门（4 请求） |
| `r3-cookie-runtime-2026-11-24.json` | 匿名 Set-Cookie 属性形状（4 请求） |
| `r3-cookie-static-2026-11-24.json` | 双应用 `ke_sess_bak`/CSP/DOMPurify 静态比对（0 请求） |
| `round3-redaction-selfcheck.json` | 三期当轮脱敏自检记录（历史产物） |

#### D. 四期离线深挖与 dry-run 计划（`evidence/round4/`，6 份 JSON，实时请求 = 0）
| 文件 | 内容与性质 |
| --- | --- |
| `r4-sms-probe-dryrun.json`、`r4-idor-token-probe-dryrun.json`、`r4-cas-probe-dryrun.json`、`r4-ratelimit-coverage-dryrun.json` | **4 份 0 请求 dry-run 探针计划**（12 + 39 + 19 + 136 项），待授权窗口按 `R4_RUN=1` 解锁实跑，**不计入运行时实证量** |
| `r4-evidence-stats.json` | 历史台账可解析子集（255 条）量化统计，静态复核 |
| `round4-redaction-selfcheck.json` | 四期当轮脱敏自检记录（历史产物） |

#### E. 五期授权窗口补盲证据（`evidence/round5/`，6 份 JSON，113 请求）
| 文件 | 视点 / 内容与请求数 |
| --- | --- |
| `r5-anon-deep-2026-11-24.json` | 视点 B 匿名面（46 请求）：两池 OpenAPI 全表/RPC 名单、T1 canary 回读、T10 schema_migrations、T13 全量 RPC、T14 GoTrue settings、T11 Storage、T12 Functions |
| `r5-anon-write-2026-11-24.json` | 视点 B 匿名面（9 请求）：T15 空对象 INSERT 写权分层判定（42501 vs 23502）+ T1 canary 精确计数（`0-1/2`） |
| `r5-session-deep-2026-11-24.json` | 视点 A 登录态（34 请求）：CAS 换票、用户面 14 RPC、用户面空 INSERT 写权、T18–T21 Moma 高危端点员工 RBAC（含正对照） |
| `r5-session-probe2-2026-11-24.json` | 视点 A 登录态（11 请求）：wechat/iam-credential/chats-proxy/load-test 响应结构（仅形状无原值）、空体 POST 门控、匿名基线对照 |
| `r5-logout-revoke-2026-11-24.json` | 视点 A 破坏性登出 v2（**8 请求**；meta 含 `data_quality_note`：v1 的 15 请求台账被同路径覆盖且无备份，T22 配对证据丢失、本文件会话起点已失效，T22 改阻塞） |
| `r5-logout-confirm-2026-11-24.json` | 视点 A 只读补采（5 请求）：登出后 TGC/saas/trusteeship 全量重定向目标与 Set-Cookie 名（修正 Location 捕获 bug，不再登出；属登出后单次快照，无同端点登出前正对照） |

> 离线工作产物（不计入 `evidence/`）：`outputs/moma-api-surface-2026-11-23/`（`static-verification-2026-11-23.json` 等，39/39 通过）、`outputs/moma-r3-surface-2026-11-24/`（三期前端公开工件）、`outputs/security-round4-workproduct/`（628 切片离线提取）、`outputs/security-local-workproduct/`（范围外本机加固回归，T7 移出）。配套协议参考：`docs/ke-app-credential-reuse.md`、`docs/crm-auth-flow-analysis.md`、`services/crm_connector/docs/`。
>
> **统一脱敏门禁（T9）**：原 round2/round3/round4 三套自检脚本已合并为 `scripts/security/redaction_gate.py`，递归扫描本审计目录全部 `*.md`/`*.json`/`*.txt`（含一期 20 JSON + 1 TXT），规则覆盖 JWT/Bearer/Cookie/CAS ST/11 位手机号/邮箱/IPv4/本机路径/通用 64hex 等；外发前须 0 命中，结果落 `redaction-gate-selfcheck.json`。

---

## 8. 修订记录

| 版本 | 日期 | 摘要 |
| --- | --- | --- |
| 2026-11-20 ~ 2026-11-23 r1–r5 | 2026-11-20 ~ 2026-11-23 | 完成初版 20 项矩阵、口径校准、单账号最小轮、跟进轮、离线深挖补充（KE-21~26/WD-01）与二期视点 B 匿名/静态/DOM 实证（KE-27~32）。 |
| **2026-11-24 r6（最终合并版）** | **2026-11-24** | ① 完整并入二期视点 A（`beike-server`）会话窗口全部结果（71 请求 + 2 轮浏览器 harness），升级 **KE-24** 为高危（登出后旧 Moma Cookie 重放仍 200）、新增 **WD-02**（豌豆上传绕过与孤儿对象清理缺口）、坐实用户面 RLS/解密与 M4 应用级并发熔断缺失、关闭 KE-05 TGC 吊销与豌豆 Bearer 登出防线；② 修正准确度收口项（KE-09/postMessage/Groovy/批量赋值 5 项改判阻塞、CORS 8 探针双维度 5/3 精确计数、SigV4 HEAD 403 归因纠偏）；③ 精简去重全文结构，清理二期重复分报告、`TASKLIST.md` 及 26 个一次性过程脚本，并将本主报告纳入 `redaction_selfcheck.py` 全量脱敏扫描。 |
| **2026-11-24 r7（三/四期并入 + 审计纠偏 + 脱敏门禁收口）** | **2026-11-24** | ① **T1–T6 纠偏**：KE-21 canary 清理口径收紧（`return=minimal` 下 204 不证 DELETE 权限，最多 2 条残留待回读）；KE-25/29 按四场景精确拆分（400/`code=1` 与无密钥退化 `data` 数组）；补实测爆炸半径精确计数（users 80,795 / usage_logs 2,239,107 等）与 phone 长度 14 形态；严重/高危 15 行重新核算并把 KE-32 移出 8 根因；RPC 超时按函数名双视点拆分；补录 GoTrue 生产回调残留 `localhost:3000`（KE-08 关联，T6）；② 并入三期结论（P0-3 脚手架系统性、应用 B 宽 RLS 复现、aroute 匿名门有效、CORS 错误分支反射）与四期 **R4-S01~S04 / R4-B01~B07**（0 实时请求，三层 taxonomy 挂靠既有编号）；③ **T17** 写入同源 UGC 结构性隔离方案（独立子域/sandbox iframe，CSP 不可根治）；**T24** 补录 4 个无 HttpOnly/Secure 遗留身份 Cookie；④ **T7/T8/T9 脱敏门禁**：范围外本机加固台账移出 evidence（二期 17→16）、低熵字段无盐 sha16 清理并改单次运行随机盐 HMAC、三套自检合并为统一 `redaction_gate.py`（新增邮箱/IPv4/本机路径/通用 64hex 规则），证据总量锁定 50 份（含 4 份 0 请求 dry-run，运行时实证 46 份）。 |
| **2026-11-24 r8（五期授权窗口 round5 补盲，113 请求）** | **2026-11-24** | 凭证恢复后双视点实测（§3.8 非破坏 100 请求 + §3.9 登出专项 13 请求，证据 +6 份至 56 份）：① **T1 坐实**——`prompts` 表精确残留 2 条 canary（`0-1/2`），两次 204 DELETE 确为 RLS 静默拒绝；② **T10** `schema_migrations`（含 `sql` 列）匿名 200 可读；③ **T13** RPC 匿名执行面：A 池 12/14、B 池 8/8 可达（含写 RPC `sync_usage_logs_city`、PII 聚合 `get_active_user_emails` 37KB / `get_user_usage_details` ≥64KB），双视点权限集合一致、仅 statement_timeout 分层（anon≈3s / authenticated≈8s）；④ **T15** 空 INSERT 分层：`users`/`admins` RLS 拒绝（42501），`usage_logs`/`prompts`/`policy_news`/`properties`/`smart_clip_jobs` 持写权（23502）；⑤ **T14** 两池 `disable_signup=false`；⑥ **T18–T21** 员工态穿透：新增 **KE-33**——`wechat/access-token` 200 直发 137 字符凭据、`iam-credential`/`load-test/run`/`chats/proxy` 过登录门无管理 RBAC，internal/admin 路径 404 存疑；⑦ **T11/T12** Storage 边缘不拦但 0 桶可见、Functions 匿名 401 拦截；⑧ **T25/T26** SRI 全缺、`/shipinshengcheng` HTML 壳 30 天长缓存；⑨ **登出专项（§3.9，破坏性，执行后会话已销毁）**：T22 因 v1 台账被 v2 同路径覆盖且无备份而**改判阻塞**（待重新扫码补采）；T23 的 TGC/豌豆/Moma 结论以二期 `round2-session-2026-11-23-logout.json` 完整 pre/post 配对为准（TGC、豌豆吊销有效；Moma 不被 CAS 联动吊销即 KE-24），Supabase JWT/saas/trusteeship 联动阻塞；⑩ 记录并修复 CAS Accept 406、`dict(HTTPMessage)` 重复 Set-Cookie 致 Location 丢失、破坏性探针同路径覆盖三个方法学问题。T27/T28 Agent 专项按授权边界另列。 |
