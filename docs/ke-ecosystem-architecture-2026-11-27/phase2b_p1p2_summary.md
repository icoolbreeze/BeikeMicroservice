# Phase 2B-P1P2（72 站）内网隧道浏览器深扫与快筛假通过复核报告

> **唯一数据基线**：本文所有统计口径严格对齐 [final_ecosystem_dataset.json](./final_ecosystem_dataset.json) 与 [site_profiles_2b_p1p2.jsonl](./site_profiles_2b_p1p2.jsonl)（共 **72** 条去重主机档案：`P1` 41 站 + `P2` 31 站）。
> **执行模式**：托管临时 SSH SOCKS5 隧道（`network_mode=socks5_tunnel`），复用 [phase2_site_profiler.py](./scripts/phase2_site_profiler.py)（`--timeout-ms 20000`），每站独立隔离 `BrowserContext`，全局拦截 `/(logout|signout|logoff)(/|$|\?)`；72 站单站累计耗时 `778.66s`（均值 `10.81s`/站，中位数 `11.41s`/站）。
> **核心结论**：经 89 站 HTTP 轻量快筛淘汰 17 站后，进入浏览器深扫的 **72 站**中，真正端到端实质可用的核心系统共 **30 站**（**A 级 8 站 + B 级 22 站**，占 `41.7%`）；另有 **11 站**虽返 HTTP 200/307 但实测处于 `/401`、`/p/noPerms`、「暂时无法访问」、接口 401、超时或二次 SSO「加载中」的 **C 级部分受限/空壳态**（`15.3%`）；其余 **31 站**在真实浏览器渲染后直接跌入业务 RBAC 无权（D 级 13 站）、边缘安全网关 `hip.ke.com` 拦截（E 级 14 站）、独立登录/跳转中断（F 级 3 站）或 404 错误（G 级 1 站），揭示了 SPA 前端鉴权体系下 HTTP 快筛高达 **`43.1%`（硬假通过 31/72）**、广义非实质可用 **`58.3%`（42/72）** 的显著假通过效应。

---

## 1. 72 站按 P1 / P2 的判定分布总表

### 1.1 89 站快筛到 72 站深扫的漏斗衔接

| 阶段与分组 | P1 | P2 | 合计 | 说明 |
| --- | ---: | ---: | ---: | --- |
| 轻量 HTTP 快筛总量（[phase2b_p1p2_screen.json](./phase2b_p1p2_screen.json)） | 47 | 42 | **89** | 涵盖财务、法务、HR、产研基建与文化行政 |
| 快筛直接淘汰（`denied_403` 3 + `login_required` 7 + `unreachable_or_error` 7） | 6 | 11 | **17** | 跳过浏览器深扫（详见第 5 节） |
| 拟进入浏览器深扫（`accessible_verified_or_page` 47 + `spa_shell_needs_browser` 25） | **41** | **31** | **72** | 与前序 2A/2B-1/P0 站点零重叠 |

### 1.2 72 站浏览器原始判定（`access_verdict`）与精细化可用等级（A–G）对照表

| 维度 / 分类 | P1（41 站） | P2（31 站） | 合计（72 站） | 占比 | 口径说明 |
| --- | ---: | ---: | ---: | ---: | --- |
| **原始判定：`authenticated_verified`** | **4** | **4** | **8** | 11.1% | 全部对应 **A 级**（认证通过且主体核验 `identity_match=true`） |
| **原始判定：`authenticated_page`** | **19** | **14** | **33** | 45.8% | 细分为 **B 级 22 站**（P1 10 + P2 12）与 **C 级 11 站**（P1 9 + P2 2） |
| **原始判定：`denied_403`** | **16** | **11** | **27** | 37.5% | 细分为 **D 级业务 RBAC 13 站**（P1 4 + P2 9）与 **E 级 HIP 拦截 14 站**（P1 12 + P2 2） |
| **原始判定：`login_required`** | **1** | **2** | **3** | 4.2% | 对应 **F 级**（独立登录或 CAS 未建会话） |
| **原始判定：`error`** | **1** | **0** | **1** | 1.4% | 对应 **G 级**（`legal-lawyer-library.ke.com` 回调 404） |
| **合计** | **41** | **31** | **72** | **100.0%** | **实质可用（A+B）= 30 站（P1 14 + P2 16）** |

> **精细化等级 A–G 汇总**：
> - **A 级（认证通过且主体核验）**：**8 站**（P1 `budget-control`、`codin`、`mknow`、`wen`；P2 `apollo.portal.life`、`cloud.intra`、`m-intelligencecampus-hq`、`nltx`）
> - **B 级（认证通过可用业务页）**：**22 站**（P1 10 站 + P2 12 站）
> - **C 级（部分受限、无权提示、未沉降或超时空壳）**：**11 站**（P1 9 站 + P2 2 站）
> - **D 级（业务 RBAC 无权）**：**13 站**（P1 4 站 + P2 9 站）
> - **E 级（边缘安全网关 `hip.ke.com` 拦截）**：**14 站**（P1 12 站 + P2 2 站）
> - **F 级（独立认证孤岛 / 跳转回落登录）**：**3 站**（P1 1 站 + P2 2 站）
> - **G 级（不可达 / 404）**：**1 站**（P1 1 站 + P2 0 站）

---

## 2. 快筛「假通过」清单与机制根因分析

在 72 个 HTTP 轻量快筛放行站点（47 个 `accessible_verified_or_page` + 25 个 `spa_shell_needs_browser`）中，浏览器深扫共揭露出 **31 站硬假通过**（终态落入 D/E/F/G，占 `43.1%`）以及 **11 站软受限/未沉降 C 级站点**（占 `15.3%`）。

### 2.1 快筛放行形态与浏览器终态交叉矩阵

| 快筛阶段判定（输入 72 站） | A 级 | B 级 | C 级（软受限） | D 级（业务 RBAC） | E 级（HIP 拦截） | F 级（登录回落） | G 级（404） | 硬假通过率（DEFG） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `P1: accessible_verified_or_page` (23) | 2 | 5 | 7 | 1 | 8 | 0 | 0 | **9 / 23 (39.1%)** |
| `P1: spa_shell_needs_browser` (18) | 2 | 5 | 2 | 3 | 4 | 1 | 1 | **9 / 18 (50.0%)** |
| `P2: accessible_verified_or_page` (24) | 4 | 9 | 1 | 7 | 2 | 1 | 0 | **10 / 24 (41.7%)** |
| `P2: spa_shell_needs_browser` (7) | 0 | 3 | 1 | 2 | 0 | 1 | 0 | **3 / 7 (42.9%)** |
| **合计 (72 站)** | **8** | **22** | **11** | **13** | **14** | **3** | **1** | **31 / 72 (43.1%)** |

### 2.2 硬假通过全量清单（共 31 站：HTTP 快筛放行 → 浏览器落入 D / E / F / G）

| # | 优先级 | 主机 (`host`) | 系统名称 | 快筛判定 | 浏览器终态等级 (`access_verdict`) | 终态 URL / 标题 / 实证阻断机理 |
| ---: | :---: | --- | --- | --- | :---: | --- |
| 1 | P1 | `okr.ke.com` | OKR | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 2 | P1 | `ones.ke.com` | KeOnes 项目管理 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 3 | P1 | `report.ke.com` | 小贝汇报 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 4 | P1 | `meeting-assistant.ke.com` | 会议室系统 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 5 | P1 | `profile-h5.ke.com` | 人才标准 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 6 | P1 | `inrecruit.ke.com` | 内部活水 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 7 | P1 | `spi.ke.com` | 运营绩效系统 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 8 | P1 | `chip.ke.com` | 电子合同下载校验 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 9 | P1 | `legal-compliance.ke.com` | 合规工作台 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 10 | P1 | `case-legal.ke.com` | 诉讼管理 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 11 | P1 | `company-legal.ke.com` | 股权管理 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 12 | P1 | `homeworld-front.ke.com` | 日常用品申请/公司菜单 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 13 | P2 | `ganbu.ke.com` | 干部画像 | `accessible_verified_or_page` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 14 | P2 | `gongyi.ke.com` | 贝壳公益 | `spa_shell_needs_browser` | **E** (`denied_403`) | 跳转 `hip.ke.com/forbidden`（`Forbidden`） |
| 15 | P1 | `renshi.ehr.lianjia.com` | LinkHR / 个人电子档案 | `accessible_verified_or_page` | **D** (`denied_403`) | CAS 签发 `ehrs`，落入 `/department/search` 无权提示 |
| 16 | P1 | `caigou.ke.com` | 采购系统 | `spa_shell_needs_browser` | **D** (`denied_403`) | 调用 16 个 `/mall/*` 公开字典接口后跳转 `/userauth/assist/403.html` |
| 17 | P1 | `zichan.ke.com` | 资产 2.0 | `spa_shell_needs_browser` | **D** (`denied_403`) | CAS 后落 `/p/home/dashboard`，页面卡片渲染 `Forbidden` |
| 18 | P1 | `shandian.lianjia.com` | 闪店 | `accessible_verified_or_page` | **D** (`denied_403`) | 虽过 `/api/util/userInfo` (`identity_match=true`)，页面仍呈现业务无权态 |
| 19 | P2 | `xinchou-data.ke.com` | 薪酬预处理平台 | `accessible_verified_or_page` | **D** (`denied_403`) | CAS 签发 `prep_token`，页面卡片渲染 `Forbidden` |
| 20 | P2 | `cb.ke.com` | 基薪管理 | `accessible_verified_or_page` | **D** (`denied_403`) | 调用 `/api/cbms/v1/auth/roles` 后页面卡片渲染 `Forbidden` |
| 21 | P2 | `leader.ke.com` | 管理者工作台 | `spa_shell_needs_browser` | **D** (`denied_403`) | `GET /api/manage/hasRole` 返回 `403/-1`，无管理权限 |
| 22 | P2 | `hrlearn.ke.com` | HR价值流 | `accessible_verified_or_page` | **D** (`denied_403`) | 调完初始化接口后跳转 `http://hrlearn.ke.com/403` |
| 23 | P2 | `vm.ke.com` | 价值观评分 | `accessible_verified_or_page` | **D** (`denied_403`) | 调 `/api/culture/saas/server/v1/permission/ids` (`401/900002`) 后渲染 `Forbidden` |
| 24 | P2 | `admin-study.ke.com` | 贝好学管理端 | `accessible_verified_or_page` | **D** (`denied_403`) | `layout/v1/get` 返回 `biz_code=403` 并跳转 `https://admin-study.ke.com/403` |
| 25 | P2 | `be-admin.ke.com` | 新经纪榜样 | `accessible_verified_or_page` | **D** (`denied_403`) | 调 `/api/culture/saas/server/v1/permission/ids` 后卡片渲染 `Forbidden` |
| 26 | P2 | `jingyingshe-admin.ke.com` | 精英社 | `accessible_verified_or_page` | **D** (`denied_403`) | 调 `/api/culture/elite/server/v1/permission/ids` 后卡片渲染 `Forbidden` |
| 27 | P2 | `kehome-cms.ke.com` | 贝壳之家 | `accessible_verified_or_page` | **D** (`denied_403`) | 调 `getUserInfo`/`role/list` 后跳转 `https://kehome-cms.ke.com/403` |
| 28 | P1 | `new-meeting.ke.com` | 会议室预定 | `spa_shell_needs_browser` | **F** (`login_required`) | `/proxy4ke/login/cas` 触发 `ERR_ABORTED`，滞留 `/login?redirect=%2Freserve` |
| 29 | P2 | `fssc.lianjia.com` | 老费控 | `accessible_verified_or_page` | **F** (`login_required`) | CAS 回调落 `http://fssc.lianjia.com/info2.jsp?sso_username=[REDACTED]`（`登陆失败提示`） |
| 30 | P2 | `admin-sunshine.ke.com` | 阳光贝壳 | `spa_shell_needs_browser` | **F** (`login_required`) | 纯前端 hash 路由 `https://admin-sunshine.ke.com/web/#/login` 独立登录页 |
| 31 | P1 | `legal-lawyer-library.ke.com` | 律师库 | `spa_shell_needs_browser` | **G** (`error`) | 虽调通 `/prod-api/getInfo`，但 `/callback` 主文档返回 `HTTP 404` |

### 2.3 软受限 / 未完成沉降清单（共 11 站：HTTP 200/307 但实测落入 C 级）

| # | 优先级 | 主机 (`host`) | 系统名称 | 快筛判定 | 浏览器终态 | 实测异常与定位说明 |
| ---: | :---: | --- | --- | --- | --- | --- |
| 1 | P1 | `bpm.ke.com` | HR 权限申请/礼品申报 | `accessible_verified_or_page` | **C** | `title="暂时无法访问"`，CAS 登录后特定流程参数页面不可达 |
| 2 | P1 | `bpm-m.ke.com` | 离职申请 | `accessible_verified_or_page` | **C** | 重定向至 `bpm.ke.com`，同为 `title="暂时无法访问"`（域合流） |
| 3 | P1 | `finops.ke.com` | 贝壳费控平台 | `accessible_verified_or_page` | **C** | CAS 后停留在 `/ecs_console/index.html#/ssoLogin`（`title="加载中"`） |
| 4 | P1 | `rpm.ke.com` | 报账平台 | `accessible_verified_or_page` | **C** | CAS 后停留在 `/ecs_console/index.html#/ssoLogin`（`title="加载中"`），签发 `agentic_token` |
| 5 | P1 | `mdm.ke.com` | 主数据管理平台 MDM | `accessible_verified_or_page` | **C** | CAS 完成后跳转至 `https://mdm.ke.com/401` |
| 6 | P1 | `sis.lianjia.com` | SIS 门店损益 | `accessible_verified_or_page` | **C** | CAS 完成后跳转至 `http://sis.lianjia.com/401` |
| 7 | P1 | `yes-or-no.ke.com` | Yes or No 员工交流 | `spa_shell_needs_browser` | **C** | `/api/likeit/server/v1/admin/getUserPerms` 返回 `400/20003` 并跳转 `/p/noPerms` |
| 8 | P1 | `m-legal.ke.com` | 合同管理（法螺） | `accessible_verified_or_page` | **C** | 20s 沉降超时，仅加载 `#/contract` 标题壳，未捕获业务 API |
| 9 | P1 | `vprivacy.ke.com` | 数据合规 | `spa_shell_needs_browser` | **C** | 20s 沉降超时，未触发 CAS 跳转（`sso=none`），空标题/0 UI/0 API |
| 10 | P2 | `esghub.ke.com` | 碳管理 | `spa_shell_needs_browser` | **C** | HTTP 307 滞留 `Loading`，`/api/hub/core/service/who-am-i` 等 5 个 API 返回 `401` |
| 11 | P2 | `shebao.ke.com` | 社保系统 | `spa_shell_needs_browser` | **C** | `POST /currentUserApi/currUser` 返回 `401`（仅 `taskCount` 200），页面未渲染 |

> **方法学启示**：仅靠 `httpx`/`urllib` 轻量 HTTP 探针在现代化 SPA + 零信任网关架构下存在两类系统性盲区：
> 1. **HIP 网关延迟阻断（14 站）**：HTTP 层首包放行或快筛仅见静态 HTML 壳，真实 Chromium 执行 JS 指纹或跳转校验时即被 `hip.ke.com/forbidden` 截断；
> 2. **前端路由级鉴权与 `/401`/`/403` 重定向（17 站 D/C + 3 站 F）**：React/Vue SPA 首包恒定返回 HTTP 200 骨架 HTML，仅在浏览器运行 JS 请求 `/auth/roles`、`/permission/ids`、`/getUserPerms` 后才跳转到无权页或 `#/login` 表单。

---

## 3. 按职能业务域归纳 72 站可用核心系统（共 30 站 A+B）

### 3.1 财务费控、资产与税务预算域（5 站实质可用）
- **`budget-control.ke.com`（预算管控系统，P1，A 级）**：CAS 签发 `budget_control`，核验主体一致；侧栏含 `文件下载`、`日志管理`，调用 `/api/auth/getLoginUser`、`/api/auth/getUserPermission`、`/budget/config/base`、`/process/budgetAssistant/access`（`200/200`）、`/process/config/getOrderData` 等 6 个业务接口。
- **`bfa.ke.com`（财务+，P1，B 级）**：CAS 签发 `koin_token`，顶栏涵盖 **7 大财务中台子域**：`对账平台`、`资金系统`、`记账平台`、`商业化`、`财务中台`、`线下收款认款`、`结算平台`，侧栏含 `常用`、`政策`、`制度`、`公告`，调用 `/api/koin/v1/config/base` 与 `/api/koin/v1/permission`。
- **`pay.ke.com`（Kepay 贝壳 Pay，P1，B 级）**：CAS 签发 `pay_ke_token`、`pay_ke_token_up_.ke.com`、`tiper_token_up_.ke.com`，侧栏含 `新建报销`、`单据列表`、`EBS-AP`、`发票列表`、`待处理`、`已处理`、`我的`，调用 `POST /papi/fssc/server/server/reimburse/v2/getUnhandleList`（`200/1`）。
- **`kepler.ke.com`（税务平台，P1，B 级）**：CAS 签发 `kepler_token`，顶栏含 `销项发票管理`、`进项发票管理`、`税务主数据管理`，侧栏含 `发票申请`、`流程中心`、`资质申请`、`手工开票管理`、`数电登录`，调用 `/api/v1/apply/pagination` 与 `/api/v2/dict`。
- **`mzichan.ke.com`（我的资产 / 资产申请，P1，B 级）**：CAS 签发 `zichan_m`，调用 `/api/asset/customer/server/v1/asset/inventoryFlag`、`/asset/list`、`/dictionary/getAssetClassification`、`/task/unaccomplished-number` 4 个资产接口（区别于管理员后台 `zichan.ke.com` 的 D 级无权）。
- **关联 C/D/F 级边界**：`finops.ke.com`（费控）与 `rpm.ke.com`（报账）共用 `ecs_console` 平台框架（C 级二次 SSO 加载态）；`mdm.ke.com` 与 `sis.lianjia.com` 落 `/401`（C 级）；`caigou.ke.com`、`zichan.ke.com`、`shandian.lianjia.com` 为业务 RBAC 拒绝（D 级）；`fssc.lianjia.com` 为老费控登录失败（F 级）。

### 3.2 法务合规与审批中台域（3 站实质可用）
- **`mknow.ke.com`（违纪通报，P1，A 级）**：CAS 签发 `SESSION` 并通过 `/web/loginUser/info`（`biz_code=1`）核验主体一致；调用 `/web/fe/knowledge/knowConfigServiceImpl/getSkin`、`knowledgeOpenApiServiceImpl/search`、`relationTreeOpenApiServiceImpl/getCatalogList`（与客服知识库 `xiaobei.ke.com` 同属一套企业级知识引擎）。
- **`contract.ke.com`（合同管理，P1，B 级）**：CAS 签发 `contract_manage_token`，**同时嵌入智能助手 `is-prod.ke.com` 会话簇**（签发 `intelligentService_sid`、`intelligentservice_token` 并跨站调用 `is-prod.ke.com/admin/skill/queryMainPage`）；侧栏含 `白名单管理`，业务接口 `/papi/contract/manage/server/v1/*` 与招聘底层共享 `/papi/zhaopin/base/server/v1/middle/org/descendant`、`referenceItems`。
- **`workflow.lianjia.com`（通用审批流，P2，B 级）**：CAS 回调 `/casLogin` 后进入 `/index`（`流程定义列表`），签发 `JSESSIONID`、`rememberMe`。
- **关联受限边界**：`legal-compliance.ke.com`、`case-legal.ke.com`、`company-legal.ke.com` 全部落入 HIP 网关拦截（E 级）；`legal-lawyer-library.ke.com` 回调 `/callback` 404（G 级）；`m-legal.ke.com`、`vprivacy.ke.com`、`bpm.ke.com`、`bpm-m.ke.com` 处于 C 级超时或提示态。

### 3.3 HR 招聘、绩效薪酬与培训成长域（11 站实质可用）
- **`codin.ke.com`（CHO 驾驶舱，P1，A 级）**：实为 **Odin 数据平台家族**（`奥丁数据分析平台`），CAS 签发 `odin_token` 与 `dwh-datascreen_token`，通过 `/ajax/user/me` 核验主体一致，调用 `/kegate/odin/agent/hasGray` 与 `/kegate/proxy/secret/ubag/common/*`。
- **`nltx.ke.com`（职级平台，P2，A 级）**：CAS 签发 `agent_score_system_sid`，调用 `/session/user`（`identity_match=true`）、`/session/user/permissions`、`/user/comp_info`、`/user/contribute/score/detail`、`/user/scoreOfLead/detail`、`/user/user_info`，首页展示 `积分概览`、`当前职级`、`当前积分`、`预计下月初职级`、`带教流水`。
- **`m-intelligencecampus-hq.ke.com`（老左图书馆，P2，A 级）** 与 **`admin-huaqiao.ke.com`（花桥学堂，P2，B 级）**：共享 `huaqiao_token` 认证体系，其中老左图书馆通过 `/api/v1/user/current` 完成主体核验并加载 `/api/v1/library/rule/list`；花桥学堂 CMS 调用 `/api/v1/common/base`、`/api/v1/user/perm`、`/api/v1/user/roles`。
- **`recruit.ke.com`（贝壳招聘 / 入职管理，P1，B 级）**：CAS 签发 `lianjia_token_zhaopin` 并嵌入 `intelligentService_sid`/`intelligentservice_token`，跨站调用 `is-prod.ke.com/admin/skill/queryMainPage`，本域调用 7 个 `/papi/i/talent/*` 与 `/papi/zhaopin/*` 接口。
- **`resume-agc.ke.com`（聘多多，P2，B 级）**：注意公网候选 `jianli.ke.com` 被 HIP 拦截，而内网入口 `resume-agc.ke.com/talent/candidateList` 可正常认证访问！签发 `mag_ke_com` 与德佑/大客户身份 Cookie 簇（`ke_dk_u_id`、`ke_dk_u_name`、`ke_do_dk_u_id`），调用 `/api/newOauth/agc/interview/api/b/agc/interview/pdd/check-in-grey` 与 `/api/oauth/talent/axe/server/v2/axe/pdd/employee/find-ucId-v2`。
- **`profile.ke.com`（人才管理，P1，B 级）**：CAS 签发 `profile_pc_token`，调用 `/api/talent/auth/getUserRoleList`。
- **薪酬中台集群（4 站 B 级）**：
  - `salary-middle.ke.com`（薪酬中心）与 `salary-tax.ke.com`（税友平台）共享 `salary_middle_token` 与同一套五模块顶栏（`薪酬中心`、`税友中心`、`核算平台`、`预处理平台`、`服务监控`）；
  - `salary-monitor.ke.com`（服务监控）签发 `salary_monitor_token` 并调用 `/api/v1/auth/role/info`、`/roles`、`/dict/all`；
  - `lcm.ke.com`（预实管理）签发 `salary_budget_actual_sid` 并嵌入 `is-prod.ke.com` 智能助手，侧栏链接薪酬中心/基础薪酬/预处理平台。（同域的 `cb.ke.com`、`xinchou-data.ke.com` 在前端权限校验后落入 D 级无权）。

### 3.4 产研基建、IT 服务与办公协作域（7 站实质可用）
- **`apollo.portal.life.ke.com`（Apollo 配置中心，P2，A 级）**：CAS 签发 `jt_apollo_login_token`，通过 `/user`（`identity_match=true`）核验主体，加载 `/appnamespaces/public`、`/apps/by-owner`、`/favorites`、`/permissions/root` 等 11 个本域接口，支持多环境配置查询。
- **`cloud.intra.ke.com`（贝壳云 / 产品树，P2，A 级）**：CAS 签发 `EGG_SESS`、`cloud_console_token_egg`、`cloud_console_user_session`，调用 `/cloud-proxy-api/user/current_user`（`200/200000`、`identity_match=true`）、`/global_search/history`、`/platform/cloud_product/visit_history`、`/getRoutersByEnvAndProgramKey`，侧栏含 `我的工单`、`我的反馈`、`我的费用`。
- **`easy.intra.lianjia.com`（链家网 EASY，P2，B 级）**：CAS 签发 `sre_login`，呈现老链家运维工具面板（`Cron任务`、`输入PROJECT & TOKEN`）。
- **`compass.ke.com`（罗盘 / 埋点平台，P2，B 级）**：CAS 签发 `compass_token`，跨站调用 `compass-server.ke.com/login/cas` 与本域 `/loginUser/info`。
- **`risk-platform.ke.com`（风控平台，P2，B 级）**：CAS 签发 `lianjia_token`，跨站跳转并调用 `public-risk-control-home.ke.com/login/cas` 与 `/perm/isSuperAdmin`。
- **`its.ke.com`（IT 服务 / 视频会议，P1，B 级）** & **`its.lianjia.com`（软件下载，P1，B 级）**：双域名共享 `its_ke_token` 与同一顶栏（`IT服务`、`常见问题`、`软件下载`、`网页邮箱`、`会议预定`），提供 IT 故障指南与全套办公软件（A+、企微、云枢、腾讯会议、Office 等）内网下载入口。

### 3.5 企业文化、调研与行政服务域（4 站实质可用）
- **`wen.ke.com`（贝壳问卷，P1，A 级）**：CAS 签发 `wenjuan_ke_com`，进入 `/home/create`，调用 `/api/taurus/server/v1/flag/admin`、`/v1/user/merchant`（主体核验一致）、`/v2/naire/queryGrayByUser`、`/v2/privilege/ehr/checkByNames`。
- **`homeworld.ke.com`（行政服务平台，P1，B 级）**：CAS 签发 `homeworld_ke_token`，展示 `行政宝典`，调用 `//user/authList` 与 `//user/list`（区别于 `homeworld-front.ke.com` 被 HIP 拦截）。
- **`mcg.ke.com`（CG 文化圈，P2，B 级）** & **`vote-admin.ke.com`（荣誉评选系统，P2，B 级）**：`mcg.ke.com` 签发 `mcg_ke_lianjia_token` 并调用 7 个文化圈动态/福利/周年弹窗 API（同簇的 `vm.ke.com` 落入 D 级无权）；`vote-admin.ke.com` 签发 `cg_ke_com` 并调用 `/api/oc/value/vote/server/p/common/v1/perms` 与 `user-roles`（同簇的 `be-admin.ke.com`、`jingyingshe-admin.ke.com` 落入 D 级无权）。

---

## 4. 72 站逐站深扫全量明细表

| # | 优先级 | 主机 (`host`) | 系统名称 | 快筛判定 | 浏览器判定 (`access_verdict`) | 终态等级 | HTTP | 主体核验 | 跨站后端 / 异常提示 |
| ---: | :---: | --- | --- | --- | --- | :---: | ---: | :---: | --- |
| 1 | P1 | `recruit.ke.com` | 招聘/入职 / 入职管理系统 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 调 `is-prod.ke.com` |
| 2 | P1 | `renshi.ehr.lianjia.com` | LinkHR / 个人电子档案 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | 无权页面 |
| 3 | P1 | `okr.ke.com` | OKR | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 4 | P1 | `ones.ke.com` | KeOnes 项目管理 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 5 | P1 | `report.ke.com` | 小贝汇报 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 6 | P1 | `bpm.ke.com` | HR 权限申请/礼品申报 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | `暂时无法访问` |
| 7 | P1 | `bpm-m.ke.com` | 离职申请 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | 跳 `bpm.ke.com` 暂时无法访问 |
| 8 | P1 | `finops.ke.com` | 贝壳费控平台 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | `#/ssoLogin` 加载中 |
| 9 | P1 | `bfa.ke.com` | 财务+ | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 7 个财务子系统入口 |
| 10 | P1 | `pay.ke.com` | Kepay | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | `getUnhandleList` 200/1 |
| 11 | P1 | `rpm.ke.com` | 报账平台 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | `#/ssoLogin` 加载中 |
| 12 | P1 | `caigou.ke.com` | 采购系统 | `spa_shell_needs_browser` | `denied_403` | **D** | 200 | `false` | `/userauth/assist/403.html` |
| 13 | P1 | `budget-control.ke.com` | 预算 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | 6 个本域预算/流程接口 |
| 14 | P1 | `zichan.ke.com` | 资产 2.0 | `spa_shell_needs_browser` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片 |
| 15 | P1 | `mzichan.ke.com` | 我的资产/资产申请 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | 4 个资产查询接口 |
| 16 | P1 | `its.ke.com` | IT 服务、视频会议 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | 共享 `its_ke_token` |
| 17 | P1 | `its.lianjia.com` | 软件下载 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | 共享 `its_ke_token` |
| 18 | P1 | `new-meeting.ke.com` | 会议室预定 | `spa_shell_needs_browser` | `login_required` | **F** | 302 | `false` | CAS 回调中断回落登录 |
| 19 | P1 | `meeting-assistant.ke.com` | 会议室系统 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 20 | P1 | `wen.ke.com` | 贝壳问卷 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | `/api/taurus/server/*` |
| 21 | P1 | `yes-or-no.ke.com` | Yes or No 员工交流 | `spa_shell_needs_browser` | `authenticated_page` | **C** | 200 | `false` | 落 `/p/noPerms` (`400/20003`) |
| 22 | P1 | `profile.ke.com` | 人才管理 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | `/api/talent/auth/getUserRoleList` |
| 23 | P1 | `profile-h5.ke.com` | 人才标准 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 24 | P1 | `inrecruit.ke.com` | 内部活水 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 25 | P1 | `spi.ke.com` | 运营绩效系统 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 26 | P1 | `codin.ke.com` | CHO 驾驶舱 | `spa_shell_needs_browser` | `authenticated_verified` | **A** | 200 | `true` | Odin 数据平台家族 |
| 27 | P1 | `chip.ke.com` | 电子合同下载校验 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 28 | P1 | `mdm.ke.com` | 主数据管理平台 MDM | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | 落 `/401` |
| 29 | P1 | `kepler.ke.com` | 税务平台 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 发票与税务主数据模块 |
| 30 | P1 | `shandian.lianjia.com` | 闪店 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `true` | 业务无权提示 |
| 31 | P1 | `sis.lianjia.com` | SIS 门店损益 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | 落 `/401` |
| 32 | P1 | `legal-compliance.ke.com` | 合规工作台 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 33 | P1 | `m-legal.ke.com` | 合同管理 | `accessible_verified_or_page` | `authenticated_page` | **C** | 200 | `false` | 20s 沉降超时 |
| 34 | P1 | `contract.ke.com` | 合同管理 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 调 `is-prod.ke.com` |
| 35 | P1 | `case-legal.ke.com` | 诉讼管理 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 36 | P1 | `company-legal.ke.com` | 股权管理 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 37 | P1 | `legal-lawyer-library.ke.com` | 律师库 | `spa_shell_needs_browser` | `error` | **G** | 404 | `false` | `/callback` 404 |
| 38 | P1 | `vprivacy.ke.com` | 数据合规 | `spa_shell_needs_browser` | `authenticated_page` | **C** | 200 | `false` | 20s 沉降超时 |
| 39 | P1 | `mknow.ke.com` | 违纪通报 | `spa_shell_needs_browser` | `authenticated_verified` | **A** | 200 | `true` | 企业知识树引擎 |
| 40 | P1 | `homeworld.ke.com` | 行政服务 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | `//user/authList` 200 |
| 41 | P1 | `homeworld-front.ke.com` | 日常用品申请/公司菜单 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 42 | P2 | `apollo.portal.life.ke.com` | Apollo 配置中心 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | 11 个 Apollo API |
| 43 | P2 | `cloud.intra.ke.com` | 贝壳云、产品树 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | `/cloud-proxy-api/*` |
| 44 | P2 | `easy.intra.lianjia.com` | 链家网 EASY | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 老链家 Cron 运维台 |
| 45 | P2 | `compass.ke.com` | 埋点平台 / 罗盘 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | 调 `compass-server.ke.com` |
| 46 | P2 | `risk-platform.ke.com` | 风控平台 | `spa_shell_needs_browser` | `authenticated_page` | **B** | 200 | `false` | 调 `public-risk-control-home.ke.com` |
| 47 | P2 | `workflow.lianjia.com` | 通用审批流 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 流程定义列表 |
| 48 | P2 | `fssc.lianjia.com` | 老费控 | `accessible_verified_or_page` | `login_required` | **F** | 200 | `false` | `登陆失败提示` |
| 49 | P2 | `xinchou-data.ke.com` | 薪酬预处理平台 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片 |
| 50 | P2 | `cb.ke.com` | 基薪管理 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片；调 `is-prod.ke.com` |
| 51 | P2 | `salary-middle.ke.com` | 薪酬中心 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 共享 `salary_middle_token` |
| 52 | P2 | `salary-monitor.ke.com` | 服务监控 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | `/api/v1/auth/roles` |
| 53 | P2 | `salary-tax.ke.com` | 税友平台 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 共享 `salary_middle_token` |
| 54 | P2 | `shebao.ke.com` | 社保系统 | `spa_shell_needs_browser` | `authenticated_page` | **C** | 200 | `false` | `currUser` 返回 401 |
| 55 | P2 | `ganbu.ke.com` | 干部画像 | `accessible_verified_or_page` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 56 | P2 | `leader.ke.com` | 管理者工作台 | `spa_shell_needs_browser` | `denied_403` | **D** | 200 | `false` | `hasRole` 403/-1；调 `is-prod.ke.com` |
| 57 | P2 | `nltx.ke.com` | 职级平台 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | 7 个积分职级 API |
| 58 | P2 | `hrlearn.ke.com` | HR价值流 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `true` | 落 `/403`；**异常调用 `test-login.ke.com`** |
| 59 | P2 | `resume-agc.ke.com` | 聘多多 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 共享 `ke_dk_u_*` 簇 |
| 60 | P2 | `vm.ke.com` | 价值观评分 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片 |
| 61 | P2 | `vote-admin.ke.com` | 荣誉评选系统 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 共享 `cg_ke_com` |
| 62 | P2 | `admin-huaqiao.ke.com` | 花桥学堂 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 共享 `huaqiao_token` |
| 63 | P2 | `admin-study.ke.com` | 贝好学管理端 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `true` | 落 `/403`；共享 `renovation-token` |
| 64 | P2 | `admin-sunshine.ke.com` | 阳光贝壳 | `spa_shell_needs_browser` | `login_required` | **F** | 200 | `false` | `#/login` 独立登录页 |
| 65 | P2 | `be-admin.ke.com` | 新经纪榜样 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片 |
| 66 | P2 | `esghub.ke.com` | 碳管理 | `spa_shell_needs_browser` | `authenticated_page` | **C** | 307 | `false` | 5 个业务接口返回 `401` |
| 67 | P2 | `gongyi.ke.com` | 贝壳公益 | `spa_shell_needs_browser` | `denied_403` | **E** | 200 | `false` | `hip.ke.com` |
| 68 | P2 | `jingyingshe-admin.ke.com` | 精英社 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `false` | `Forbidden` 卡片 |
| 69 | P2 | `kehome-cms.ke.com` | 贝壳之家 | `accessible_verified_or_page` | `denied_403` | **D** | 200 | `true` | 落 `/403` |
| 70 | P2 | `lcm.ke.com` | 预实管理 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 调 `is-prod.ke.com` |
| 71 | P2 | `m-intelligencecampus-hq.ke.com` | 老左图书馆 | `accessible_verified_or_page` | `authenticated_verified` | **A** | 200 | `true` | 共享 `huaqiao_token` |
| 72 | P2 | `mcg.ke.com` | CG文化圈 | `accessible_verified_or_page` | `authenticated_page` | **B** | 200 | `false` | 7 个文化圈 API |

---

## 5. 快筛直接淘汰 17 站闭环回顾

89 站 P1/P2 候选中，以下 **17 站**在 [phase2b_p1p2_screen.json](./phase2b_p1p2_screen.json) 阶段已直接确认为不可用，未进入浏览器深扫：
- **独立登录 / 认证孤岛（`login_required`，7 站）**：`mail.ke.com`（P1，Outlook OWA）、`owa.lianjia.com`（P1，Outlook OWA）、`wiki.lianjia.com`（P1，Confluence 独立登录页）、`dawei.ke.com`（P2，大为 Innojet 专利系统独立登录）、`weapons.ke.com`（P2，落兵台 CAS 重定向循环）、`hecapp.homelink.com.cn`（P2，AFA 爱发独立登录页）、`zhidao.ke.com`（P2，贝经堂跳转独立登录域 `zlogin.ke.com`）。
- **无权或拒绝（`denied_403`，3 站）**：`ketalk.ke.com`（P1，HTTP 403）、`admin.uc.lianjia.com`（P2，跳转 `/noauth`）、`ireport.lianjia.com`（P2，跳转 `/401`）。
- **不可达或 404（`unreachable_or_error`，7 站）**：`yunpan.lianjia.com`（P1，302 异常）、`zjjg.homelink.com.cn`（P1，连接断开）、`design.ke.com`（P2，404）、`pm.ke.com`（P2，TLS EOF）、`recruit-ops.ke.com`（P2，协议畸形响应）、`solicitude.hr.lianjia.com`（P2，404）、`gongwei.ke.com`（P2，404）。

---

## 6. 内存级 PII 清洗复查与脱敏门禁状态

1. **内存级 PII 专项复查**：已通过 [review_phase2b_p1p2_pii.py](./scripts/review_phase2b_p1p2_pii.py) 在内存中比对当前授权账号姓名、UCID、工号、手机号、邮箱及部门名称对 [site_profiles_2b_p1p2.jsonl](./site_profiles_2b_p1p2.jsonl) 全部 `title`、`ui_modules` 与 `backend_apis` 字段执行了清洗复查，确认落盘文件中不含任何真实 PII 或登录名原值（如 `finops.ke.com` 与 `rpm.ke.com` 的 `loginName` 与 `sign` 参数已清洗为 `[REDACTED]`）。
2. **离线脱敏自检通过**：[phase2b_p1p2_redaction_check.json](./phase2b_p1p2_redaction_check.json) 与 [phase2_all_redaction_check.json](./phase2_all_redaction_check.json) 均记录全工作区 20 个交付物扫描结果 `passed == true`、`findings == []`。
3. **跨阶段衔接**：本批次 72 站数据已全部并入 [final_ecosystem_dataset.json](./final_ecosystem_dataset.json)，与 Batch 2A（22 站）及 Batch 2B-1/P0（72 站）完成去重合并，构成 **156 独立深扫站 + 17 快筛淘汰站 = 173 站点**全量闭环。
