# 目标企业全生态旁路测绘与系统架构全景（2026-11-27）

> **范围声明**：本目录归档的是**目标企业全生态信息系统的旁路发现、实登功能拆解与整体系统架构分析**，**并非 `BeikeMicroservice` 项目本身的服务架构**。
> 全部数据经严格只读探索、内存级 PII 清洗与离线脱敏门禁校验（见 [final_redaction_check.json](./final_redaction_check.json)：`passed: true, findings: []`），供后续 Agent 直接查阅、复核与增量完善。

---

## 1. 核心实证结论速览

| 维度 | 实测精确口径（唯一数据源：[final_ecosystem_dataset.json](./final_ecosystem_dataset.json)） |
| --- | --- |
| **Phase 2 候选全量闭环** | **173** 个内部 Web 候选（**156** 个独立浏览器深扫主机 + **17** 个 P1/P2 HTTP 快筛淘汰主机），另登记 12 个未重测历史 `not_counted`、5 个显式别名、30+ 个底层后端/网关域、35 个外部 SaaS 与关联品牌域 |
| **156 站七级可用性分布（A–G）** | **A 级 38**（主体一致）+ **B 级 53**（可用业务页）+ **C 级 15**（受限/未沉降/空壳）+ **D 级 19**（业务 RBAC 403）+ **E 级 23**（HIP 安全网关拦截）+ **F 级 3**（独立登录）+ **G 级 5**（不可达/404） |
| **实质可用总量（A + B）** | **91 个可用入口主机（占比 58.3%）**，扣除 2 组重定向合流后为 **89 个独立可用业务系统实体** |
| **双网边界分布** | **公网直连可用 11 站**（原 5 基线 + 新增 6 站，另有 1 个轻应用托管根路径为 C 级空壳）+ **内网隧道独有可用 80 站** = **91 站** |
| **原 28 入口纠偏与遗漏核查** | 原 28 表实证纠偏 4 项（2 站业务无权降 D 级、1 站认证空壳降 C 级、1 站重定向合流归并），实际余 **24 个独立可用业务实体**，覆盖率仅 **`27.0%`（24 / 89）**；本次**净新增 65 个独立可用业务系统** |
| **关键异常与架构信号** | 16 组跨站共享业务 Cookie 簇；92.3%（84/91）可用站走统一 CAS；P1/P2 纯 HTTP 快筛硬假通过率高达 **43.1%（31/72）**；实测捕获 2 处生产入口运行时请求测试域的异常信号 |

---

## 2. 后续 Agent 推荐阅读顺序

1. **[03-enterprise-system-architecture.md](./03-enterprise-system-architecture.md)**（**主架构报告**）：四张 Mermaid 架构图（全局四层全景、IAM/CAS 与异构会话流转、双网旁路外溢与 HIP 拦截、三代技术栈与品牌演进收敛）与 6 条实证架构洞察。
2. **[01-omissions-and-inventory.md](./01-omissions-and-inventory.md)**（**遗漏核查与全量资产台账**）：原 28 入口 4 项纠偏明细、173 站闭环对账、公网/内网/后端底座/不可用边界四象限台账。
3. **[02-bypass-capability-catalog.md](./02-bypass-capability-catalog.md)**（**功能与接口手册**）：全量 91 个可用旁路 + 15 个 C 级站点按 7 大业务域逐站拆解（定位、网络边界、认证/Cookie 族、UI 模块、实测 XHR API 与跨站拓扑）。
4. **[final_ecosystem_dataset.json](./final_ecosystem_dataset.json)**（**唯一机器可读事实源**）：后续 Agent 做二次统计、子域过滤、安全面筛选或生成可视化看板时，直接解析此文件即可，无需重复抓取。

---

## 3. 目录文件索引

| 类别 | 文件 | 说明 |
| --- | --- | --- |
| **核心报告** | [03-enterprise-system-architecture.md](./03-enterprise-system-architecture.md) | 目标企业全生态架构全景报告（含 4 张 Mermaid 图与 6 条深度洞察） |
| | [01-omissions-and-inventory.md](./01-omissions-and-inventory.md) | 28 入口遗漏核查、纠偏项与全量分层资产台账 |
| | [02-bypass-capability-catalog.md](./02-bypass-capability-catalog.md) | 7 大业务域 91 可用旁路 + 15 C 级站功能与 API 手册 |
| **核心脱敏数据** | [final_ecosystem_dataset.json](./final_ecosystem_dataset.json) | 聚合去重后的全量事实数据库（156 深扫 + 17 淘汰 + 基线） |
| | `site_profiles_2a.jsonl` | 公网直连 22 站浏览器测绘逐站档案 |
| | `site_profiles_2b.jsonl` | 内网隧道 72 站（2B-1 33 站 + P0 39 站）逐站档案 |
| | `site_profiles_2b_p1p2.jsonl` | 内网隧道 72 站（P1 41 站 + P2 31 站）逐站档案 |
| | `phase1_discovery.json` / `phase2b_p1p2_screen.json` | 7 大枢纽全量链接提取 / P1&P2 89 站轻量 HTTP 快筛明细 |
| | `phase2*_targets.json` / `phase2a_handoff_to_2b.json` | 各批次输入目标清单与公网转内网交接清单 |
| | [final_redaction_check.json](./final_redaction_check.json) | 全目录离线脱敏自检证明（`passed: true, findings: []`） |
| **阶段报告与契约** | [phase1_diff.md](./phase1_diff.md)、[phase2a_summary.md](./phase2a_summary.md)、[phase2b_p1p2_screen.md](./phase2b_p1p2_screen.md)、[phase2b1_p0_summary.md](./phase2b1_p0_summary.md)、[phase2b_p1p2_summary.md](./phase2b_p1p2_summary.md) | 各阶段过程汇报（完整保留供审计追溯） |
| | `TASK_SPEC.md`、`PHASE2_SPEC.md`、`PHASE2B_SPEC.md`、`PHASE2B_NEXT_SPEC.md`、`PHASE2B_P1P2_SPEC.md`、`PHASE3_SPEC.md` | 各阶段执行契约与红线规范 |
| **可复用工具脚本** | `scripts/phase1_hub_discovery.py`、`scripts/phase2_site_profiler.py`、`scripts/phase2b_http_screener.py` | 枢纽发现、支持 `--use-tunnel --resume` 的通用 Playwright 测绘器、HTTP 快筛器 |
| | `scripts/build_phase3_dataset.py`、`scripts/review_phase2b_p1p2_pii.py`、`scripts/redaction_check.py` | 离线数据合并、内存级 PII 清洗、离线脱敏门禁扫描 |

---

## 4. 后续 Agent 增量分析与复跑规范

### 4.1 强制安全与脱敏红线
1. **严格只读、禁触登出**：所有浏览器脚本必须保持全局 `route.abort()` 拦截登出路径，严禁吊销 `beike-server` 线上 `crm-connector` 归档会话。
2. **凭证仅驻内存**：通过 SSH 别名 `beike-server`（见 [服务器连接说明](../deployment-servers.md)）仅在内存中读取会话材料，绝不落盘、绝不输出到日志。
3. **落盘前双门禁**：任何新增实测 `.jsonl` / `.json` / `.md` 落盘后，必须依次执行：
   - 内存级 PII 清洗复查：`python -B scripts/review_phase2b_p1p2_pii.py --jsonl <目标.jsonl>`
   - 离线脱敏自检：`python -B scripts/redaction_check.py --output final_redaction_check.json`（确认 `passed == true` 且 `findings == []`）
4. **纯离线重算总库**：增补站点档案后，在本目录直接运行 `python -B scripts/build_phase3_dataset.py` 即可 0 网络请求重算 `final_ecosystem_dataset.json`（脚本已适配相对路径，依赖 `../crm-sso-entry-review-2026-11-26/summary.json` 与 `../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md`）。
5. **运行环境说明**：本目录不随仓库提交 `.venv/`；如需跑 Playwright 浏览器测绘，可使用本机已安装 Playwright/httpx 的隔离虚拟环境运行 `scripts/phase2_site_profiler.py`。

### 4.2 建议后续 Agent 继续完善的 6 个高价值方向
1. **Grade C 15 站沉降与二段登录核验**：重点排查二段 SSO 加载滞留站、20 秒沉降超时站及返回 401 鉴权头差异的站点。
2. **生产引流测试域专项核查**：跟进主架构报告洞察六记录的 2 处生产入口运行时请求测试域异常（成因、是否可达、是否存在测试面暴露）。
3. **新增 6 个公网可用系统的安全基线审查**：对照 [安全审查主报告](../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md) 方法论，评估其 CORS、Cookie 安全属性、登出失效一致性与越权边界。
4. **13 个新挖隐性后端网关的接口契约测绘**：梳理本次从 156 站 XHR 流量新发现的跨站共享后端域及其认证依赖关系（明细见 [01-omissions-and-inventory.md](./01-omissions-and-inventory.md) 第 2.3 节）。
5. **同业务异域绕过 HIP 对照核验**：核实已发现的 3 组“同业务一域被 HIP 拦、另一域内网可通”的策略差异，以及 23 个 HIP 拦截站在企业零信任终端上的真实可达边界。
6. **区域关联品牌独立域扩展**：如需覆盖第 5 类记录的上海德佑独立 CAS 集群，可在获得单独授权后单列批次测绘并合入总库。

---

## 5. 关联仓库资料
- 历史 28 入口初筛复核：[crm-sso-entry-review-2026-11-26/README.md](../crm-sso-entry-review-2026-11-26/README.md)
- CRM 周边应用凭证复用入口：[ke-app-credential-reuse.md](../ke-app-credential-reuse.md)
- 安全审查最终合并报告：[kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md](../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md)
