# 企业系统生态旁路发现与架构测绘任务契约（TASK_SPEC）

## 0. 工作空间隔离与红线（强制）
1. **目录严格隔离**：所有新脚本、中间数据、报告与日志**仅允许写入** `C:\Users\shipi\AI-work\ke-arch-20261127\`。**绝对禁止修改、删除或新建** `C:\Users\shipi\vswp\BeikeMicroservice\` 下的任何文件（仅可只读参考其历史文档与脚本）。
2. **只读探索红线**：
   - 严禁访问、点击或触发任何 `/logout`、`/signout`、`/logoff` 或包含「退出登录」的链接/请求（防止吊销线上正在运行归档服务的统一会话）。
   - 严禁提交表单、发起增删改写操作、发送站内信/IM/短信或上传文件。
   - Playwright 必须配置全局路由拦截器，自动 `abort()` 任何路径命中 `/(logout|signout|logoff)(/|$|\?)` 的请求。
3. **凭证与隐私脱敏红线**：
   - 仅在内存中读取现有已授权会话材料，**绝不落盘、绝不打印到 stdout/stderr**。
   - 所有落盘的 JSON / JSONL / Markdown 文件中，**禁止出现**任何真实 TGC、TGC_Secure、ST 票据、Cookie 原值、Bearer Token、JWT、手机号、真实姓名、邮箱、身份证号、银行卡号、工号/UCID 原值或消息正文。
   - 所有 URL 落盘前必须清洗敏感查询参数（`ticket`、`token`、`code`、`access_token`、`refresh_token`、`signature`、`credential` 等替换为 `[REDACTED]` 或剔除查询串，仅保留无敏感参数的结构化路径与无害业务标识）。
   - 主体核验只输出布尔值 `identity_match: true/false`，不输出任何个人字段值。
4. **临时网络隧道托管与清理**：
   - 访问需办公内网可达的目标时，在 Python 脚本内通过 `try...finally` 托管临时动态端口转发隧道（SSH 配置见只读参考文档 `deployment-servers.md`），脚本退出（含异常退出）前必须确保关闭该子进程并验证端口已释放，绝不遗留僵尸进程。
   - 熔断机制：若连续 3 个已知应认证通过的枢纽页面全部回落至登录页，立即中止执行并报错，禁止反复重试刷新或触发验证码。

---

## 1. 只读参考资料（位于 BeikeMicroservice，严禁改动）
- **现有 28 入口基线与 15 未计入候选**：
  - `C:\Users\shipi\vswp\BeikeMicroservice\docs\crm-sso-entry-review-2026-11-26\summary.json`
  - `C:\Users\shipi\vswp\BeikeMicroservice\docs\crm-sso-entry-review-2026-11-26\README.md`
  - 同目录下的 `direct-browser.json`、`focused-browser.json`、`internal-browser.json`、`commercial-browser.json`、`public-app-browser.json`
- **凭证内存读取、Cookie 注入与脱敏写法参考**：
  - `C:\Users\shipi\vswp\BeikeMicroservice\scripts\security\adjacent_page_audit_2026_11_26.py`
  - `C:\Users\shipi\vswp\BeikeMicroservice\docs\ke-app-credential-reuse.md`
- **服务器 SSH 别名与远端凭证位置参考**：
  - `C:\Users\shipi\vswp\BeikeMicroservice\docs\deployment-servers.md`
- **历史安全审计主报告（用于补齐已文档化的后端/网关域名做交叉去重）**：
  - `C:\Users\shipi\vswp\BeikeMicroservice\docs\kecom-security-audit-2026-11-20\kecom-security-audit-report-2026-11-20.md`

---

## 2. 全流程分阶段规划
- **Phase 1（本轮执行）**：只深度登录 **6 大聚合枢纽门户**（+ A+ 客户端认证服务底座），抓取其完整导航菜单、应用卡片、外链域名及导航配置 XHR 接口，并与 `summary.json`（28 已确认 + 15 未计入）及历史审计报告中的已知主机做差集对比，回答「28 入口是否存在遗漏」。
- **Phase 2（待用户确认 Phase 1 差集后再启动）**：分网络域（Batch 2A 公网直连 / Batch 2B 云端内网）串行批量测绘所有确认目标的功能模块、菜单结构、后端 API 域与认证机制，逐站落盘 `site_profiles.jsonl`（支持 `--resume` 断点续跑）。
- **Phase 3（待 Phase 2 完成后离线执行）**：纯离线综合 Phase 1 + Phase 2 结构化数据与历史基线，产出完整系统功能手册、遗漏总表与企业整体系统架构文档（含 Mermaid 架构图）。

---

## 3. Phase 1 详细执行规范（当前任务）

### 3.1 目标范围（仅限以下枢纽，严禁在 Phase 1 批量扫全部 28+ 站点）
从只读参考 `summary.json` 动态解析以下 7 个入口地址（若门户包含已知二级主页路径如员工门户 `/home/portal` 与 `/myInfo/profile` 则一并覆盖）：
1. **工作台首页**（`summary.json` 中的工作台首页入口）：提取渲染后的全部顶部/侧边导航、工作台应用卡片、快捷入口链接与关联菜单 XHR 接口。
2. **新房 / A+ 统一顶栏**（`summary.json` 中的新房入口）：重点捕获全站顶栏接口（如 `/menuService/getTopNavList`）完整菜单树（仅保留菜单名与脱敏 URL，过滤 PII）及渲染后顶栏链接。
3. **员工门户**（`summary.json` 中的员工门户入口，覆盖 `/myInfo/profile` 与 `/home/portal`）：重点捕获 `/api/tenant/nav` 全部导航项、`/api/user/profile` 中的 `myPackages` 应用包名称与链接（严禁落盘 `user` 个人资料！）、以及页面全部应用卡片链接。
4. **HR 门户**（`summary.json` 中的 HR 门户入口）：提取登录后首页渲染出的全部 HR 子应用链接、菜单项及菜单 XHR 接口。
5. **AI 门户**（`summary.json` 中的 AI 门户入口）：提取全部分类区块（内部应用专区、AI 模型应用、AI 办公工具、AI 图像工具等）下的**所有应用卡片标题、所属分类、目标 URL 与主机名**。
6. **客服中心 / 业绩争议**（`summary.json` 中的客服中心入口）：提取登录后首页导航与 `/web/fe/helpcenter/moduleConfigServiceImpl/getHomePageConfig` 返回的模块链接（脱敏去 PII）。
7. **A+ 客户端认证服务**（`summary.json` 中的 A+ 客户端认证服务入口）：轻量确认其渲染后页面标题、实际内容形态、可见链接与自动触发的 API，厘清其架构定位。

### 3.2 采集技术要求
1. 优先复用本机已有 Python 环境与 Playwright（可先检查 `py`、`BeikeMicroservice` 内的 `.venv` 等，但**不要在 `BeikeMicroservice` 内安装或修改任何包**）。
2. 通过只读读取远端会话材料至内存，为浏览器上下文注入必要认证材料；所有内网枢纽通过脚本托管的临时本地 SOCKS5 隧道访问；同时对公网枢纽（如 AI 门户、客服中心）尽量标注直连可达性。
3. 单枢纽页面硬超时 30 秒，等待 SPA 渲染与异步菜单接口沉降（`networkidle` 或关键接口返回 + 2–3 秒缓冲，防止超时阻塞）。
4. 对每个枢纽记录以下结构化字段（写入 `phase1_discovery.json`）：
   - `hub_key`, `hub_name`, `entry_url`, `final_url`（脱敏）, `title`, `http_status`, `authenticated`（bool）, `identity_match`（bool）, `auth_cookies_issued`（仅 Cookie 名称数组，无值）
   - `observed_api_endpoints`（去重后的 `[method, host, path, status, biz_code]`）
   - `extracted_links`（去重数组：每条含 `source` [dom_nav / dom_card / xhr_api], `category`, `label`, `sanitized_url`, `host`, `is_internal_corp_domain`）
   - `errors`（如有，脱敏）
5. **差集分析逻辑**：
   - 将 7 个枢纽提取的全部主机 + 历史审计报告中记录的内部业务/网关主机汇总去重；
   - 与 `summary.json` 的 28 个已确认主机（含其 `entries` 别名主机）和 15 个 `not_counted` 主机逐一比对；
   - 将所有未出现在 28 主机清单中的发现清晰划分为 5 类：
     1. **新发现的内部 Web 业务入口（遗漏候选，建议纳入 Phase 2 实登验证）**
     2. **已文档化但未进 28 表的核心后端/API 网关/轻应用平台**
     3. **此前标记为 `page_only_unverified` 且在枢纽中再次出现的 SPA 子系统**
     4. **现有 28 主机的别名/旧入口域名**
     5. **外部第三方 SaaS 链接（非目标企业域）**

### 3.3 Phase 1 交付物与自检门禁（必须全部落地在 `C:\Users\shipi\AI-work\ke-arch-20261127\`）
1. `scripts/phase1_hub_discovery.py`：清晰可复用的 Phase 1 采集与差集脚本（含 `/logout` 拦截、隧道 `try...finally` 清理、严格脱敏）。
2. `scripts/redaction_check.py`：离线脱敏自检脚本，扫描 `ke-arch-20261127` 下全部 `.json` / `.jsonl` / `.md` 文件（检测 `TGT-`、`ST-`、JWT `eyJ...`、常见 Cookie/Token 赋值、11 位手机号形态、邮箱、身份证号/银行卡形态、本机私钥路径等），**严禁回显命中原文**，仅输出 `{passed, scanned_files, findings: [{file, line, rule, match_sha256_12}]}`，并保存结果至 `phase1_redaction_check.json`。
3. `phase1_discovery.json`：脱敏后的 7 枢纽完整结构化采集数据与差集清单。
4. `phase1_diff.md`：中文阶段汇报文档，包含：
   - 7 枢纽登录与提取状态总表；
   - 5 类遗漏与差集清单（重点列出 AI 门户、员工门户、工作台、客服中心新挖出的全部内部主机及所属功能标签）；
   - 供用户确认的 Phase 2 目标清单与分网批次建议（Batch 2A 公网直连 / Batch 2B 内网隧道）；
   - 脱敏自检通过证明与临时隧道清理确认。

### 3.4 验收标准
- `phase1_discovery.json`、`phase1_diff.md`、`phase1_redaction_check.json` 均已生成且非空；
- `phase1_redaction_check.json` 中 `passed == true` 且 `findings` 为空；
- 确认 `C:\Users\shipi\vswp\BeikeMicroservice` 的 `git status` 未产生任何新增改动；
- 确认本机无残留的临时 SSH 隧道进程。
