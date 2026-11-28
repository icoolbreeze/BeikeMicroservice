# Phase 3 执行契约（纯离线全量综合：P1/P2 收口 + 遗漏总表 + 旁路功能手册 + 企业系统架构全景）

> **绝对红线（纯离线模式）**：
> - **0 网络请求、0 SSH 连接、0 浏览器启动、0 凭证读取**！本阶段完全基于工作区已有脱敏数据做离线计算与架构综合。
> - 仅在 `C:\Users\shipi\AI-work\ke-arch-20261127\` 下新建或修改交付物，**严禁改动** `C:\Users\shipi\vswp\BeikeMicroservice\` 下任何文件（仅可只读参考历史架构与安全文档做上下文补充）。
> - 所有最终产物必须 100% 通过 `scripts/redaction_check.py` 脱敏门禁（`passed == true`、`findings == []`），绝不落盘任何 PII、Cookie/Token 原值或敏感参数。

---

## 0. 已收齐的全量实测数据资产（均已完成内存级 PII 复查与脱敏自检）
1. **Phase 1 枢纽门户全量发现与差集**：
   - `phase1_discovery.json`（7 大枢纽完整渲染 DOM + 菜单配置 XHR + 差异主机原始映射）
   - `phase1_diff.md`
2. **Phase 2A 公网直连 22 站深扫**：
   - `site_profiles_2a.jsonl`（22 行去重记录）
   - `phase2a_summary.md` & `phase2a_handoff_to_2b.json`
3. **Phase 2B-1（33 站）+ P0 核心业务（39 站）内网深扫**：
   - `site_profiles_2b.jsonl`（72 行去重记录）
   - `phase2b1_p0_summary.md`
4. **Phase 2B-P1P2 轻量快筛（89 站）+ 浏览器深扫（72 站）**：
   - `phase2b_p1p2_screen.json` & `phase2b_p1p2_screen.md`（含 17 站快筛淘汰记录）
   - `site_profiles_2b_p1p2.jsonl`（72 行去重记录，已完成内存级 PII 清洗）
5. **只读历史参考基线**（位于 `BeikeMicroservice`，只读）：
   - `C:\Users\shipi\vswp\BeikeMicroservice\docs\crm-sso-entry-review-2026-11-26\summary.json`（原 28 已确认 + 5 别名 + 15 未计入）
   - `C:\Users\shipi\vswp\BeikeMicroservice\docs\kecom-security-audit-2026-11-20\kecom-security-audit-report-2026-11-20.md`（历史审计记录的后端网关/轻应用/统一接口层/即时通讯网关等架构事实）

---

## 1. 离线合并与统计校验（先用脚本保证 100% 算术精确）
编写轻量离线聚合脚本 `scripts/build_phase3_dataset.py` 读取上述全部 JSON/JSONL，输出一份统一的脱敏全量事实汇总 `final_ecosystem_dataset.json`，作为所有最终 Markdown 文档的**唯一精确数据来源**（防止手算统计出错、条目重漏或口径不一致）：
- 精确去重合并 `site_profiles_2a.jsonl`（22 行）、`site_profiles_2b.jsonl`（72 行）、`site_profiles_2b_p1p2.jsonl`（72 行）：
  - 对同时在 2A（公网）和 2B（内网）测过的主机（6 个转入站 + 4 个公网拦截复核站），**保留双网络视点对照字段**（`direct_verdict` vs `intranet_verdict`），并以最终有效态汇总，按 `host` 精确去重（预期去重后独立深扫主机为 `22 + 72 + 72 - 10 = 156` 个）；
  - 并入 `phase2b_p1p2_screen.json` 中 17 个未进浏览器深扫的淘汰站点、原 `summary.json` 中 15 个 `not_counted` 站点、以及第 2 类核心后端/API 网关/静态域、第 4 类别名域、第 5 类外部 SaaS/品牌域，确保全局账目 100% 闭环对平。
- 自动抽取并统计：
  - 每个独立主机的最终可用等级（A：认证通过且主体核验 / B：认证通过可用业务页 / C：部分受限、无权提示或空壳 / D：业务 RBAC 无权 / E：边缘安全网关 HIP 拦截 / F：独立认证孤岛 / G：不可达或 404/5xx）；
  - 每个系统观测到的 SSO 机制、签发 Cookie 名簇（自动聚合共享同一认证 Cookie 或同一后端 API 域的隐性系统集群，例如智能服务令牌集群、数据平台令牌集群、企业文化后台集群、A+ 统一导航与角色切换底座等）；
  - 全量跨站后端 API 调用拓扑（哪些前端调用了哪些跨站共享微服务后端域，以及实测观测到的生产入口引流测试/预发域等异常信号）。

---

## 2. Phase 3 必须交付的 5 份最终成果（全部位于 `C:\Users\shipi\AI-work\ke-arch-20261127\`）

### 2.1 `phase2b_p1p2_summary.md`（补齐 Phase 2B-P1P2 72 站阶段汇报）
- 72 站按 P1（41 站）/ P2（31 站）的判定分布总表；
- 明确列出“HTTP 快筛放行但在浏览器渲染后实际落入无权/403/独立登录/HIP 拦截”的假通过清单（揭示 SPA 前端鉴权与 HTTP 快筛的差异）；
- 按职能域（财务费控资产、法务合规合同、HR 招聘绩效薪酬、产研基建与办公协作、企业文化与行政服务）归纳真正可用的核心系统、功能模块与跨站共享后端域；
- 记录内存级 PII 清洗复查结果与脱敏自检通过状态。

### 2.2 `01-omissions-and-inventory.md`（回答第一问：原有 28 入口遗漏核查与全量资产台账）
- 开篇直接给出精准对比结论：原有 28 入口清单的真实覆盖率、本次浏览器深扫的纠偏项（实测无权或业务拦截的入口、重定向合流归并的入口、确认空壳的认证底座）以及净新增可用旁路系统总数；
- **全量分层资产总表**（按可用性象限分类）：
  1. **公网直连可用旁路清单**（详细区分原 5 个公网基线 + 本次新确认的全部公网可用业务/AI/客服系统）；
  2. **内网实证可用系统清单（原 28 表更新版 + 新发现 P0/P1/P2 可用系统）**，按业务域分组，标明入口 URL、页面标题、核验状态与别名归并；
  3. **核心后端 API 网关 / 轻应用运行时 / 支撑底座清单**（汇总历史审计 + 本轮从 156 站 XHR 流量中新挖出的全部隐性微服务/网关域名）；
  4. **不可用与受限系统边界台账**（严格区分：HIP 安全网关拦截 vs 业务侧 RBAC 无权 vs 独立登录认证孤岛 vs 退役/404/传输不可达 vs 外部 SaaS 与上海德佑关联品牌域集群）。

### 2.3 `02-bypass-capability-catalog.md`（回答第二问：各可用旁路系统功能拆解与接口手册）
- 面向**全部已确认可用的旁路系统**（覆盖原 28 表可用站 + 新发现可用站，按 **7 大业务域**组织）：
  1. 房源、新房与交易核心作业域（A+ / Link / 德佑 / 商办新版）
  2. 客源、线索、触达与协同通讯域
  3. 商业化展位、规则风控与运营工具域
  4. AI 原生平台、数据智能与分析域
  5. 员工门户、HR 人事、考勤薪酬与培训绩效域
  6. 财务费控、资产、法务合规与审批中台域
  7. 产研基建、企业文化与行政服务域
- 每个系统精炼且准确地呈现：**系统定位、网络边界（公网直连/内网隧道）、认证模式与签发 Cookie 族、页面实测功能模块/菜单结构、核心后端 API 域与代表路径、以及与其他系统的跳转/数据关联**（所有事实均严格来源于 `final_ecosystem_dataset.json` 与已有实证，严禁编造未观测到的功能）。

### 2.4 `03-enterprise-system-architecture.md`（回答第三问：目标企业整体系统架构全景报告）
- **明确声明**：本文整理的是目标企业全生态企业信息系统架构（非 `BeikeMicroservice` 项目本身）。
- 必须包含至少 **4 张高质量 Mermaid 架构图**（语法严格合法、节点命名简洁清晰、无特殊字符破坏渲染）：
  1. **全局四层企业系统架构全景图**（双网接入与安全网关层 → 统一身份认证与异构会话层 → 七大业务域与应用集群 → 共享中台网关与基础设施层）；
  2. **统一身份认证（IAM / CAS）与跨系统会话/令牌流转图**（呈现统一 CAS 主干通兑、A+ 会话底座、豌豆跨体系令牌复用、轻应用与企业文化平台的火山引擎 Supabase 认证链、以及多个独立认证孤岛）；
  3. **网络隔离与「系统旁路」外溢拓扑图**（直观展示为何存在公网直连旁路：哪些业务/AI/客服/商办系统暴露在公网边界并反向连接内部能力，以及 HIP 安全网关的拦截边界）；
  4. **三代技术栈与品牌演进收敛图**（老链家 Link 时代 → 德佑/A+ SaaS 与 Stencil Shadow DOM 微前端时代 → 贝壳平台化与 AI 原生时代）。
- **深度架构洞察章节**：基于 156 站全量实测数据提炼 5–6 条高价值架构特征（三代并存与域收敛规律、跨站共享 Cookie 簇揭示的隐性平台家族、双网边界错位形成的旁路图谱、HIP 零信任安全网关与业务 RBAC 的双层拦截格局、以及观测到的生产引流测试域等异常架构信号）。

### 2.5 `final_redaction_check.json`（最终全工作区离线脱敏门禁）
- 运行 `scripts/redaction_check.py --output final_redaction_check.json` 扫描整个工作区全部 `.json` / `.jsonl` / `.md` 文件，必须确认 `passed == true` 且 `findings == []`。

---

## 3. 验收标准
- `scripts/build_phase3_dataset.py`、`final_ecosystem_dataset.json`、`phase2b_p1p2_summary.md`、`01-omissions-and-inventory.md`、`02-bypass-capability-catalog.md`、`03-enterprise-system-architecture.md`、`final_redaction_check.json` 全部生成且非空；
- 全文所有数字统计前后完全一致且与 `final_ecosystem_dataset.json` 精确对平；
- `final_redaction_check.json` 确认 `passed == true`、`findings == []`；
- 全程 0 网络请求、`BeikeMicroservice` 零改动。
