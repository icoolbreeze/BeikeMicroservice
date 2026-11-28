# Phase 2B-1 全量（33 站）与 P0 核心业务（39 站）内网隧道深扫阶段报告

> **执行模式**：内网托管临时 SSH SOCKS5 隧道（`network_mode=socks5_tunnel`），复用 [phase2_site_profiler.py](./scripts/phase2_site_profiler.py)（`--use-tunnel --resume --timeout-ms 20000`），每站独立隔离 `BrowserContext`，全局拦截 `/(logout|signout|logoff)(/|$|\?)`（全程 `blocked_logout_requests=0`），内存读取会话凭证不落盘。
> **数据载体**：[site_profiles_2b.jsonl](./site_profiles_2b.jsonl) 已全量落盘 **72** 行去重主机档案（`2B-1` 33 站 + `2B-P0` 39 站），72 站单站实测耗时累计 `790.61s`（均值 `10.98s`/站、中位数 `10.74s`/站）。

---

## 第一部分：Phase 2B-1 全部 33 站深扫结论

### 1.1 2B-1 判定分布汇总（按来源子集拆分）

| 来源分组 (`category`) | 站点数 | `authenticated_verified` | `authenticated_page` | `denied_403` | `login_required` | `error` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `confirmed_28_intranet`（原 28 表内网站点） | **23** | 13 | 8 | 2 | 0 | 0 |
| `handoff_from_2a`（2A 直连受阻转入） | **6** | 0 | 4 | 1 | 0 | 1 |
| `denied_403_recheck`（2A 公网拦截内网复核） | **4** | 0 | 0 | 4 | 0 | 0 |
| **合计（2B-1）** | **33** | **13 (39.4%)** | **12 (36.4%)** | **7 (21.2%)** | **0 (0.0%)** | **1 (3.0%)** |

### 1.2 2B-1 全部 33 站逐站测绘总表

| # | 主机 (`host`) | 系统名称 | 来源分类 (`category`) | HTTP | 终态 URL (`final_url`) | SSO 类型 | 判定 (`access_verdict`) | 主体核验 | API 域数 / 接口数 | 耗时 |
| ---: | --- | --- | --- | ---: | --- | --- | --- | :---: | :---: | ---: |
| 1 | `shouye.link.lianjia.com` | 工作台首页 | `confirmed_28_intranet` | 200 | `https://shouye.link.lianjia.com/shou/index` | `cas_redirect` | `authenticated_verified` | `true` | 5 / 12 | 12.83s |
| 2 | `trusteeship.link.lianjia.com` | 托管业务系统 | `confirmed_28_intranet` | 200 | `https://trusteeship.link.lianjia.com/home` | `none` | `authenticated_verified` | `true` | 1 / 10 | 11.21s |
| 3 | `link.fang.lianjia.com` | 新房 / A+ 统一顶栏 | `confirmed_28_intranet` | 200 | `https://link.fang.lianjia.com/map` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 9 | 10.49s |
| 4 | `ke.deyoulife.com` | 德佑客源系统 | `confirmed_28_intranet` | 200 | `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 12 | 20.47s |
| 5 | `ke.link.lianjia.com` | 客源系统 | `confirmed_28_intranet` | 200 | `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 12 | 21.83s |
| 6 | `hetong.link.lianjia.com` | 签约合同系统 | `confirmed_28_intranet` | 200 | `https://hetong.link.lianjia.com//index` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 4 | 12.2s |
| 7 | `clue.a.ke.com` | 客户公海 / 我的潜在客户 | `confirmed_28_intranet` | 200 | `https://clue.a.ke.com/page/thread` | `cas_redirect` | `authenticated_verified` | `true` | 3 / 8 | 10.21s |
| 8 | `xiaoxi.link.lianjia.com` | 消息系统 | `confirmed_28_intranet` | 200 | `https://xiaoxi.link.lianjia.com//trend/index` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 11.87s |
| 9 | `house.link.lianjia.com` | 房源系统 | `confirmed_28_intranet` | 200 | `https://house.link.lianjia.com/search/sale/default/gdiv_mt` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 26 | 13.87s |
| 10 | `lease-pz.link.lianjia.com` | 普租租赁系统 | `confirmed_28_intranet` | 200 | `https://lease-pz.link.lianjia.com/rent/house/list?isSaaS=false` | `cas_redirect` | `authenticated_page` | `false` | 3 / 5 | 18.88s |
| 11 | `odin.ke.com` | Odin 数据平台 / 管理驾驶舱 | `confirmed_28_intranet` | 200 | `https://odin.ke.com/` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 7 | 11.77s |
| 12 | `kemcc.ke.com` | 通知中心（kemcc） | `confirmed_28_intranet` | 200 | `https://kemcc.ke.com/templatesManage` | `cas_redirect` | `authenticated_page` | `false` | 2 / 4 | 16.63s |
| 13 | `family.ke.com` | 员工门户 | `confirmed_28_intranet` | 200 | `https://family.ke.com/home/portal` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 3 | 12.77s |
| 14 | `hr.ke.com` | HR 门户（KeHR） | `confirmed_28_intranet` | 200 | `https://hr.ke.com/home/index` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 16.79s |
| 15 | `saas.a.ke.com` | A+ 客户端认证服务 | `confirmed_28_intranet` | 200 | `http://saas.a.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 2 / 2 | 4.62s |
| 16 | `8.lianjia.com` | 积分提佣 | `confirmed_28_intranet` | 200 | `http://8.lianjia.com/pageo/error/unauthorized` | `cas_redirect` | `denied_403` | `true` | 2 / 8 | 7.87s |
| 17 | `brand.ke.com` | 金贝展位推广平台 | `confirmed_28_intranet` | 200 | `https://brand.ke.com/#/index/home` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 10 | 11.41s |
| 18 | `kaoqin.hr.ke.com` | 查看假期余额 / 查看出勤记录 | `confirmed_28_intranet` | 200 | `http://kaoqin.hr.ke.com/attendance/calendar` | `cas_redirect` | `authenticated_page` | `false` | 2 / 9 | 10.38s |
| 19 | `study.ke.com` | 贝壳学堂 / 在线学习 | `confirmed_28_intranet` | 200 | `https://study.ke.com/courseList` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 10 | 12.44s |
| 20 | `newhouse-cps.ke.com` | 商圈房源聚焦 | `confirmed_28_intranet` | 200 | `https://newhouse-cps.ke.com/grid/focus/admineActivity` | `cas_redirect` | `authenticated_page` | `false` | 2 / 5 | 16.14s |
| 21 | `xinchou.ke.com` | 薪酬核算系统 | `confirmed_28_intranet` | 200 | `https://xinchou.ke.com/#/` | `cas_redirect` | `authenticated_page` | `false` | 2 / 4 | 17.28s |
| 22 | `liulian.ke.com` | 展位管理后台 | `confirmed_28_intranet` | 200 | `http://liulian.ke.com/static/buy/commentExpo` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 4 | 8.62s |
| 23 | `naturaloppty.ke.com` | 二手自然商机展位 | `confirmed_28_intranet` | 200 | `https://oomp.ke.com/#/naturalOppty/businessopp/focus/overview` | `cas_redirect` | `denied_403` | `false` | 3 / 7 | 20.07s |
| 24 | `chat-excel.ke.com` | CHAT-EXCEL | `handoff_from_2a` | 200 | `https://chat-excel.ke.com/` | `none` | `authenticated_page` | `false` | 0 / 0 | 9.94s |
| 25 | `codelink.ke.com` | CodeLink | `handoff_from_2a` | 404 | `https://codelink.ke.com/` | `unknown` | `error` | `false` | 0 / 0 | 4.2s |
| 26 | `ketest-pro.ke.com` | Ketest Copilot | `handoff_from_2a` | 200 | `https://ketest-pro.ke.com/#/auth?...` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 20.07s |
| 27 | `speechhub.ke.com` | 贝壳语音 | `handoff_from_2a` | 200 | `http://speechhub.ke.com/` | `none` | `authenticated_page` | `false` | 1 / 8 | 9.76s |
| 28 | `app-fly.ke.com` | 员工文化应用（员工关系/新文化内核/品牌资源） | `handoff_from_2a` | 200 | `https://hip.ke.com/forbidden?...` | `supabase_cas` | `denied_403` | `false` | 3 / 3 | 7.28s |
| 29 | `jixiao.ke.com` | 绩效与试用期系统 | `handoff_from_2a` | 200 | `https://jixiao.ke.com/p/home` | `cas_redirect` | `authenticated_page` | `false` | 2 / 2 | 10.04s |
| 30 | `ai-sec.ke.com` | 贝壳安全AI助手 | `denied_403_recheck` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.87s |
| 31 | `bella.ke.com` | Bella（招聘与面试 AI 助手） | `denied_403_recheck` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.75s |
| 32 | `jianli.ke.com` | 聘多多系统 | `denied_403_recheck` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.9s |
| 33 | `opengpt.ke.com` | ChatGPT 4 | `denied_403_recheck` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.87s |

### 1.3 原 28 表 23 个内网站点的功能模块、后端 API 域与权限变化

1. **A+ 核心经纪作业集群（多系统共享 Stencil Shadow DOM 与统一导航底座）**：
   - **`shouye.link.lianjia.com`（工作台首页，`authenticated_verified`）**：通过 `login.ke.com` CAS 登录签发 `JSESSIONID`、`MAIN_JSESSIONID`、`SESSION`、`saas_token`、`task-databoard-cookie` 等 10 枚 Cookie，聚合调用 5 个域 12 个接口：`session-saas-a.lianjia.com`（`/api/navigation`、`/api/getRoleSwitchOptions`）、`main.link.lianjia.com`（`/broker/change/[REDACTED]/`）、`shouye.link.lianjia.com`（`/cards/front`、`/pc/index/navigation`、`/pc/index/panels/internal`、`/pc/personal/info/Index`）及 `xiaobei.ke.com`（`/web/fe/an/knowledge/knowledgeLabelServiceImpl/getKeyValueListFromConfig`、`/web/loginUser/checkUserLogin`）。
   - **`house.link.lianjia.com`（房源系统，`authenticated_verified`）**：终态 `/search/sale/default/gdiv_mt`（标题 `德佑-房源`），检测到 Shadow DOM，捕捉到 **26 个**后端接口横跨 4 个域，涵盖房源核验（`/truehouse/count`、`/truehouse/isSupport`）、风控提示（`/pc/risk/getRiskInfoV3`）、推广与挂牌（`/promotion/banner`、`/promotion/screen`）、智能边栏（`/maintainwork/pc/housedel/smartside/apps/query`）、导航与检索（`/saas/nav/getMenu`、`/search/getSearchCondition`、`/search/getSearchFilters`）以及 `main.link.lianjia.com`、`session-saas-a.lianjia.com`。
   - **`ke.deyoulife.com` 与 `ke.link.lianjia.com`（德佑/链家客源系统，`authenticated_verified`）**：两个入口在内网均收敛至 `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer`（Shadow DOM），均调用 `ke.deyoulife.com/customer-infrastructure/*`（客源列表、按钮权限、筛选条件、AI 报表入口）、`main.deyoulife.com/broker/change/[REDACTED]/` 与 `session-saas-a.deyoulife.com/api/getRoleSwitchOptions`。
   - **`hetong.link.lianjia.com`（签约合同系统，`authenticated_verified`）**：终态 `//index`（标题 `签约服务-vr`，Shadow DOM），签发 `signapptob_token`，调用 `/navigation/menuLinks`、`main.link.lianjia.com` 与 `session-saas-a.lianjia.com`。
   - **`clue.a.ke.com`（客户公海 / 线索，`authenticated_verified`）**：终态 `/page/thread`（Shadow DOM），侧栏与筛选包含 `全部`、`求购`、`求租`、`出售`、`出租`、`线索来源`、`线索级别`、`意向区域`；后端调用 `clue.a.ke.com`（`/api/clue/sea/public/list`、`/selections`、`/api/pc/header/navigation`、`/skin`）与 `session-saas-a.ke.com/api/getRoleSwitchOptions`。
   - **`link.fang.lianjia.com`（新房 / A+ 统一顶栏，`authenticated_verified`）**：调用 `link.fang.lianjia.com`（`/api/apollo/getConfig`、`/api/common/judgeCityOpen`、`/api/config/getGlobalConfig`、`/api/front/grayProject`、`/api/pc/cityList`、`/api/project/filterMapOption`、`/api/userinfo`、`/menuService/getTopNavList`）。
   - **`xiaoxi.link.lianjia.com`（消息系统，`authenticated_page`）**：终态 `//trend/index`，顶栏具备 `首页`、`房源`、`客源`、`签约`、`签后`、`应用`、`图灵`、`消息`，主界面模块为 `系统消息`。
2. **托管、普租与数据/通知/门户体系**：
   - **`trusteeship.link.lianjia.com`（托管业务系统，`authenticated_verified`）**：终态 `/home`，顶栏含 `首页`、`客源`、`省心租工作台`、`线索`、`签后`、`人事服务`、`应用`、`消息`，侧栏含 `房源`、`合同`、`财务`、`业绩`、`客户管理`，卡片含 `数据统计`、`收房 出房 在管`、`待办事项`、`审批流`；调用 10 个本域 `/api/*` 接口（均 `biz_code=100000`）。
   - **`lease-pz.link.lianjia.com`（普租租赁系统，`authenticated_page`）**：终态 `/rent/house/list?isSaaS=false`，调用 `lease-pz.link.lianjia.com`（`/api/header/headerData`、`/api/houseList/search/listSecondLevelTag`、`/api/puzuHouse/puzu/house/auth/switchList`）及 `main.link.lianjia.com/broker/detail/[REDACTED]/`（期间伴随一次对 `house.link.lianjia.com` 租房检索跳转的 `ERR_ABORTED`）。
   - **`odin.ke.com`（Odin 数据平台，`authenticated_verified`）**：顶栏含 `首页`、`工作台`、`数据查询`、`数据魔方`、`数据探查`、`社区`；调用 `/ajax/user/me` 及 `/kegate/odin/*`、`/kegate/proxy/odin/ubag/common/*` 共 6 个本域接口（`biz_code=1`）。
   - **`kemcc.ke.com`（触达/通知中心，`authenticated_page`）**：终态 `/templatesManage`，调用 `/api/permission/getPermsByRoleId`、`/api/permisson/getPermByRole`、`/api/permisson/getRoleByUcid`。
   - **`family.ke.com`（员工门户，`authenticated_verified`）**、**`hr.ke.com`（KeHR，`authenticated_page`）**、**`saas.a.ke.com`（A+ 客户端认证服务，`authenticated_page`）**：均维持 Phase 1 架构定位，其中 `hr.ke.com` 返回 `人事管理（15）`、`招聘管理（8）`、`薪酬绩效`、`培训发展` 全量系统卡片及 `/graphql`、`/portal/systems/all` 接口。
3. **人事考勤、薪酬、培训与展位营销类（重点含 2 站权限受限实况）**：
   - **`kaoqin.hr.ke.com`（考勤 SAAS，`authenticated_page`）**：终态 `/attendance/calendar`，模块包含 `考勤打卡`、`考勤记录`、`余额`、`我的考勤日历`、`考勤审批`、`考勤总览`、`排班管理`、`年假/调休/育儿假`；调用 7 个 `/attendance/path/*`、`/balance/path/*`、`/employee/*`、`/permission/roleAndPerm` 本域接口（`biz_code=100000`）。
   - **`study.ke.com`（贝壳学堂，`authenticated_verified`）**：终态 `/courseList`，展示在线课堂与专项培训课程，调用 9 个 `/api/gbc/renovation/*` 与 `/web/login/user/info` 接口。
   - **`brand.ke.com`（金贝展位推广平台，`authenticated_verified`）**：终态 `/#/index/home`，模块含 `首页`、`展位管理`、`展位报买`、`个人中心`，调用 9 个 `/api/budget/*`、`/api/perbid/*`、`/api/user/*` 接口（`biz_code=1`）。
   - **`newhouse-cps.ke.com`（商圈房源聚焦，`authenticated_page`）**：终态 `/grid/focus/admineActivity`（`配置管理系统`），侧栏含 `展位配置平台`、`自然展位管理`、`商业展位管理`、`POC管理`、`数据管理`、`新房CPT`、`服务者认证管理`、`商圈聚焦`（场次/现场/复盘）；调用 `/api/wolverine/houseFocus/*`（其中 `queryActivityList` 一次返回 `biz_code=0`、一次返回 `biz_code=500`）。
   - **`xinchou.ke.com`（薪酬核算系统，`authenticated_page`）**：顶栏含 `首页`、`人事`、`薪酬`、`考勤`、`绩效`、`员工关怀`，侧栏可见 `岁岁有余`、`工资卡号`、`点我修改`、`语音验证码`；调用 `/sapi/sm/ps/v2/salary/landing`、`/sapi/v1/salary/menu`、`/sapi/v1/salary/user/verify`。
   - **`liulian.ke.com`（展位管理后台，`authenticated_verified`）**：终态 `http://liulian.ke.com/static/buy/commentExpo`（`留链-展位报买`），CAS 签发 `SESSIONID`，调用 `/dmp/queryDisplayInfo` 与 `/dmp/userPos`（`biz_code=1`）。
   - **`8.lianjia.com`（积分提佣，`denied_403` — 权限不足跳转未授权）**：经 CAS 登录后 `/session/user`（`identity_match=true`）与 `/calwage/isNewJifen`（`biz_code=2`）判定当前账号无积分提佣权限，自动调用 `/error/unauthorized` 并跳转至 `http://8.lianjia.com/pageo/error/unauthorized`。
   - **`naturaloppty.ke.com`（二手自然商机展位，`denied_403`）**：先调用 `naturaloppty.ke.com/business/queryShopAndTeam` 与 `/common/version/notice`，随后经 `oomp.ke.com/login/cas` 跳转至 `https://oomp.ke.com/#/naturalOppty/businessopp/focus/overview`（`商机运营管理平台`），调用 `oomp.ke.com/api/privilege/allRole` 后落入无权限态（`denied_403`）。

### 1.4 6 个 2A 转入站与 4 个公网拦截站在内网的真实结论

1. **6 个 2A 转入站（`handoff_from_2a`）内网复测结果**：
   - **4 站内网可达并已登录/加载（`authenticated_page`）**：
     - `chat-excel.ke.com`：2A 时 `ERR_CONNECTION_CLOSED`，内网返回 HTTP `200`（直出页面 `CHAT-EXCEL`，无额外 XHR）。
     - `ketest-pro.ke.com`（Ketest Copilot）：2A 时 `ERR_CONNECTION_CLOSED`，内网返回 HTTP `200`（`ketest一站式质量平台`），通过 `login.ke.com` CAS 接入 `ketest.api.ke.com/authagent-api/auth2/` 与 `/check/`。
     - `speechhub.ke.com`（贝壳语音）：2A 时 `ERR_EMPTY_RESPONSE`，内网返回 HTTP `200`（`SpeechHub · 贝壳语音算法团队`，Shadow DOM），展示 `Skills & CLI`、`算法能力`（ASR/TTS/VPR/VAD/SLM/AAU）、`能力体验`、`接口文档`、`业务场景`（智能外呼/VR签约/VR带看/AI工牌/400话务）、`语音评测`，调用 8 个本域前端路由。
     - `jixiao.ke.com`（绩效与试用期系统）：2A 时 `ERR_CONNECTION_CLOSED`，内网经 CAS 跳转 `https://jixiao.ke.com/p/home`（`职能绩效系统`），调用 `jixiao.ke.com/user/logined`（`biz_code=100001`）并渲染统一顶栏。
   - **1 站内网仍被安全网关拦截（`denied_403`）**：
     - `app-fly.ke.com`（员工文化应用）：内网可建连并触发 `login.ke.com` CAS -> `app-moma.ke.com/falcon/supabase-volc/.../auth/v1/authorize`（`sso_type=supabase_cas`）及 `ai-security-data.ke.com/api/v1/collect/report`，但最终重定向至 `https://hip.ke.com/forbidden?...`。
   - **1 站内网返回 404（`error`）**：
     - `codelink.ke.com`（CodeLink）：2A 时 `ERR_CONNECTION_CLOSED`，内网隧道下可达服务器但返回 `HTTP 404`（无页面导航或业务卡片）。

2. **4 个 2A 公网拦截站（`denied_403_recheck`）内网复核结论**：
   - `ai-sec.ke.com`（贝壳安全AI助手）、`bella.ke.com`（Bella）、`jianli.ke.com`（聘多多系统）、`opengpt.ke.com`（ChatGPT 4）在通过 `beike-server` 内网 SOCKS5 隧道访问时，**全部 4 站（100%）依然跳转至 `https://hip.ke.com/forbidden?...`（`Forbidden`）**。
   - **结论**：该 4 个站点的 HIP（`hip.ke.com`）拦截**并非仅由公网源 IP 触发**，在当前 `beike-server` 隧道出口网络与浏览器上下文下同样被安全规则阻断，需通过企业内网终端零信任/客户端侧进一步核查准入策略。

---

## 第二部分：P0 核心业务 39 站深扫结果与系统解析

### 2.1 P0 39 站判定分布

| 判定分类 (`access_verdict`) | 站点数 | 占比 | 说明 |
| --- | ---: | ---: | --- |
| `authenticated_verified` | **10** | 25.6% | CAS 认证成功且从 API/DOM 命中员工主体核验 |
| `authenticated_page` | **18** | 46.2% | HTTP 200 可达且已通过认证或免登录直出（含 1 站落 `/notauth`、1 站标题为 `错误`） |
| `denied_403` | **8** | 20.5% | 4 站触发 `hip.ke.com` / `hip.lianjia.com` 安全拦截；4 站落入业务无权限页 |
| `login_required` | **0** | 0.0% | 无独立登录回落 |
| `error` | **3** | 7.7% | 3 站传输层连接关闭或 SOCKS5 目标建连失败 |
| **合计（2B-P0）** | **39** | **100%** | — |

### 2.2 P0 核心业务 39 站逐站测绘总表

| # | 主机 (`host`) | 系统名称 | HTTP | 终态 URL (`final_url`) | SSO 类型 | 判定 (`access_verdict`) | 主体核验 | API 域数 / 接口数 | 耗时 |
| ---: | --- | --- | ---: | --- | --- | --- | :---: | :---: | ---: |
| 1 | `notice.ke.com` | 任务模块 / 新个人信息模块 | 200 | `https://notice.ke.com/sasspc/profile` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 8.26s |
| 2 | `storm.link.lianjia.com` | 业绩排名 | 200 | `https://storm.link.lianjia.com/middle/yeji/index` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 4 | 14.78s |
| 3 | `xinfang.a.ke.com` | 新房工作台 | 200 | `https://xinfang.a.ke.com/` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 7 | 17.95s |
| 4 | `yezhu.link.lianjia.com` | 业主 | — | `https://yezhu.link.lianjia.com/` | `unknown` | `error` | `false` | 0 / 0 | 1.13s |
| 5 | `oomp.ke.com` | 二手自然商机展位 | 200 | `https://oomp.ke.com/#/naturalOppty/businessopp/focus/overview` | `cas_redirect` | `denied_403` | `false` | 2 / 5 | 16.31s |
| 6 | `link.lianjia.com` | Link | 200 | `https://shouye.link.lianjia.com/shou/index` | `cas_redirect` | `authenticated_verified` | `true` | 5 / 12 | 16.7s |
| 7 | `gte.lianjia.com` | 北京交易系统 | 200 | `https://gte.lianjia.com/` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 11.16s |
| 8 | `e-contract.ke.com` | 上海合同系统 | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 4.54s |
| 9 | `deal.fang.lianjia.com` | 新房成交管理系统 | 200 | `https://deal.fang.lianjia.com/` | `cas_redirect` | `authenticated_verified` | `true` | 4 / 8 | 12.31s |
| 10 | `pan.fang.lianjia.com` | 贝壳新房录盘系统 lupan | 200 | `https://hip.lianjia.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 4.5s |
| 11 | `newcms.fang.lianjia.com` | 新房内容配置后台 CMS | 200 | `https://hip.lianjia.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 4.03s |
| 12 | `cms.newhouse-bd.ke.com` | 客发工作台 | 200 | `http://cms.newhouse-bd.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 17.47s |
| 13 | `price.lianjia.com` | 成交价管理 | 200 | `http://price.lianjia.com/fee/forbidden` | `cas_redirect` | `denied_403` | `false` | 2 / 3 | 8.76s |
| 14 | `morg.lianjia.com` | 银行管理平台 | 200 | `http://morg.lianjia.com/error/noPermission` | `cas_redirect` | `denied_403` | `false` | 1 / 1 | 13.13s |
| 15 | `intcms.jiaoyi.lianjia.com` | 交易配置平台 | 200 | `http://intcms.jiaoyi.lianjia.com/?ticket=[REDACTED]` | `cas_redirect` | `authenticated_page` | `false` | 1 / 2 | 5.49s |
| 16 | `user-ntcs.gte.lianjia.com` | 郑州交易可视化系统 | 200 | `https://user-ntcs.gte.lianjia.com/list` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 17.07s |
| 17 | `xfp.ke.com` | 上海新房提佣核算 | 200 | `https://xfp.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 8.96s |
| 18 | `ishop.lianjia.com` | 智慧门店 | 200 | `https://ishop.lianjia.com/admin/area` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 8.86s |
| 19 | `ke-map.ke.com` | 壳慧图 | 200 | `https://ke-map.ke.com/admin/home` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 20.09s |
| 20 | `lead.ke.com` | 口袋楼盘 | 200 | `https://lead.ke.com/` | `cas_redirect` | `authenticated_verified` | `true` | 3 / 4 | 10.99s |
| 21 | `loushu.lianjia.com` | 链楼书 | 200 | `https://loushu.lianjia.com/admin/home?mineTab=history&storyTab=storeHouse` | `cas_redirect` | `authenticated_page` | `false` | 2 / 10 | 16.54s |
| 22 | `wrule.ke.com` | 贝壳规则 | 200 | `https://wrule.ke.com/#/` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 13 | 9.98s |
| 23 | `rulemanage.ke.com` | 同业经纪人查询 | 200 | `https://rulemanage.ke.com/#/widget/common/black/list` | `cas_redirect` | `authenticated_page` | `false` | 2 / 5 | 15.05s |
| 24 | `wi.ke.com` | WI 分 | 200 | `https://wi.ke.com/` | `cas_redirect` | `denied_403` | `false` | 1 / 1 | 8.74s |
| 25 | `b.ke.com` | 币平台 | 200 | `http://b.ke.com/` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 5 | 10.35s |
| 26 | `biz-data.ke.com` | 业务事件平台 | 200 | `https://biz-data.ke.com/event` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 11.76s |
| 27 | `data.ke.com` | 新房业务、每日关注 | 200 | `https://data.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 8.47s |
| 28 | `fast.ke.com` | 天眼系统 | 200 | `http://fast.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 1 / 9 | 12.47s |
| 29 | `flint.lianjia.com` | 火石作业物料 | 200 | `https://flint.lianjia.com/?from=link_home` | `cas_redirect` | `authenticated_page` | `false` | 2 / 3 | 15.3s |
| 30 | `haoma.lianjia.com` | 免打扰电话查询 | 200 | `https://haoma.lianjia.com/` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 8.1s |
| 31 | `heimingdan.lianjia.com` | 黑名单 | 200 | `http://heimingdan.lianjia.com/#/` | `cas_redirect` | `authenticated_verified` | `true` | 3 / 13 | 10.0s |
| 32 | `hx.ke.com` | 红星运营平台 | 200 | `https://hx.ke.com/notauth` | `cas_redirect` | `authenticated_page` | `false` | 2 / 2 | 13.0s |
| 33 | `limu.ke.com` | 立木 | 200 | `https://limu.ke.com/recommend` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 8 | 15.21s |
| 34 | `lingjing.ke.com` | 立木 / lingjing | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 5.38s |
| 35 | `ncrm.lianjia.com` | 小贝知道 | — | `http://ncrm.lianjia.com/crm#/handbook` | `unknown` | `error` | `false` | 0 / 0 | 0.17s |
| 36 | `pref.merlin.lianjia.com` | Merlin 数据 | — | `http://pref.merlin.lianjia.com/` | `unknown` | `error` | `false` | 0 / 0 | 0.18s |
| 37 | `content.lianjia.com` | 便民服务 | 200 | `https://content.lianjia.com/service/order/print` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 2 | 10.11s |
| 38 | `cms-tv.ke.com` | 大屏二手房源/新房视频轮播 | 200 | `https://cms-tv.ke.com/carousel/new` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 7.03s |
| 39 | `mobile.lianjia.com` | LINK-APP | 200 | `https://mobile.lianjia.com/#/detail/link_secondhand` | `none` | `authenticated_page` | `false` | 0 / 0 | 2.95s |

### 2.3 P0 可用业务系统功能模块、认证方式与后端 API 域摘要

在 39 个 P0 站点中，共有 **28 站**返回 HTTP 200 可用页面（除 `hx.ke.com` 跳转 `/notauth` 无业务角色、`xfp.ke.com` 呈现错误提示页外，实际具备可用功能或看板入口共 **26 站**），几乎全部采用 `login.ke.com` CAS 统一认证（仅 `mobile.lianjia.com` 为免 CAS 直出静态下载页）。按业务域归纳如下：

1. **工作台入口与业绩/个人中心**：
   - **`link.lianjia.com`（`authenticated_verified`）**：CAS 登录后直接跳转至 `https://shouye.link.lianjia.com/shou/index`（签发 `link-lianjia` 及工作台全套 Cookie），调用完全相同的 5 个后端域（`shouye.link.lianjia.com`、`session-saas-a.lianjia.com`、`main.link.lianjia.com`、`xiaobei.ke.com`、`login.ke.com`），证实其为工作台主域别名入口。
   - **`storm.link.lianjia.com`（签中中台 / 业绩排名，`authenticated_verified`）**：CAS 签发 `storm_token`、`JSESSIONID`、`rememberMe`，调用 `storm.link.lianjia.com/cashcow/betula/main/getIndexTabList`、`POST /cashcow/betula/main/queryMonthPerformanceRank` 与 `/cashcow/birch/leftTree/auth`。
   - **`notice.ke.com`（个人/任务模块，`authenticated_page`）**：CAS 签发 `task-databoard-cookie`，渲染个人基础信息页面。

2. **新房作业、成交、楼书与地图工具矩阵**：
   - **`xinfang.a.ke.com`（新房工作台，`authenticated_verified`）** & **`deal.fang.lianjia.com`（新房成交管理系统，`authenticated_verified`）**：两站共用同一跨站微服务后端族（4 个 API 域），包括 `darius.api.ke.com`（`POST /apollo/config`）、`deal.fang.lianjia.com`（`/api/city/list`、`/api/common/redirectUrl`、`/api/menu/list`、`/api/utility/env`、`/newapi/cityconfig/qianceIsopencity`）与 `xinfang.a.ke.com`（`/api/menu`、`/api/subapps`、`/api/currentServerConfig/publicReminder/list`）。
   - **`cms.newhouse-bd.ke.com`（客发工作台，`authenticated_page`）**：CAS 签发 `CMS_NEWHOUSE_BD_TOKEN`，调用 `cms.newhouse-bd.ke.com/api/auth/check`。
   - **`lead.ke.com`（口袋楼盘，`authenticated_verified`）**：CAS 签发 `ke_lead_token`，侧栏含 `首页`、`自定义地图`、`门店商圈图`、`社区配套图`、`新房项目分布图` 及企微扫码分享；后端调用 `lead.ke.com`（`/authApi/sqt/getUser`、`POST /getUserInfo`）与高德接口 `restapi.amap.com/v3/log/init`。
   - **`loushu.lianjia.com`（链楼书，`authenticated_page`）**：侧栏含 `首页`、`精选楼书`、`楼书管理`、`浏览历史`、`个人版`、`收藏`、`草稿`、`团队版`；调用 9 个本域 `/api/ljzc/resblock/album/*` 接口（含部分权限接口返回业务码 `CD_15_1526000`）。
   - **`ke-map.ke.com`（壳慧图，`authenticated_page`）**、**`ishop.lianjia.com`（智慧门店，`authenticated_page`）**、**`data.ke.com`（看板，`authenticated_page`）**、**`cms-tv.ke.com`（门店培训大屏内容管理系统，`authenticated_page`）**：均通过 CAS 认证（分别签发 `lianjia_token_employee`、`lianjia_token`、`board_data_unify`、`cms-tv_ke_com`）。
   - **`xfp.ke.com`（上海新房提佣核算，`authenticated_page`）**：前端标题显示 `错误`（含 `Back Home`），但后端经 CAS 正常调用了 `i-wp.ke.com`（`/ljzc/xf/performance/server/perm/getPerm`、`/sysParameter/list`），揭示其跨站依赖 `i-wp.ke.com` 计佣微服务域。

3. **交易中后台、规则合规与数据风控平台**：
   - **`gte.lianjia.com`（北京交易系统 / 签后，`authenticated_page`）**、**`intcms.jiaoyi.lianjia.com`（交易配置平台，`authenticated_page`）**、**`user-ntcs.gte.lianjia.com`（郑州交易可视化系统，`authenticated_page`）**：分别签发 `gte_servers`、`ntcms_session`、`ke_ntcs_token`。
   - **`wrule.ke.com`（贝壳规则，`authenticated_verified`）** & **`heimingdan.lianjia.com`（黑名单/规则公示，`authenticated_verified`）**：两站共用「贝壳规则」门户模板与接口族（`/web/fe/rule/ruleCommonServiceImpl/*`、`/web/fe/rule/rulePcServiceImpl/*`），涵盖 `规则动态`、`平台监察`、`规则解读`、`案例解读`、`大案通告`、`曝光台`、`ACN规则`、`奖惩规则` 等模块。**关键发现**：`heimingdan.lianjia.com` 在页面加载过程中发起了对云内网测试/预发环境域 `rule-chat.ttb.test.ke.com/api/chat/fixed_script`（302）的调用，属于生产入口引用 `*.ttb.test.ke.com` 测试域实例。
   - **`rulemanage.ke.com`（蓝鲸 / 同业经纪人查询，`authenticated_page`）**：CAS 签发 `SESSIONID`，调用 `/web/cas`、`/web/loginUser/checkUserLogin`、`/web/loginUser/info`、`/web/loginUser/menus`。
   - **`fast.ke.com`（FAST 天眼系统，`authenticated_page`）**：CAS 签发 `_secondx`、`session_id`，调用 `/app/issuperadmin`、`/app/user/searchAppByUser`、`/dict`、`/statis/getDefaultAppCode`、`/statis/resource/user`、`/user` 等 9 个本域接口。
   - **`biz-data.ke.com`（业务事件平台，`authenticated_page`）**：CAS 签发 `datacenter_token`，侧栏含 `业务概览`、`查询申请`、`订阅管理`、`事件重放`、`质量监控`、`发布方管理`、`接入管理`，首页覆盖 20 余条事件发布方业务域（房源/客源/签中/新房/商业化/C端/人力/司南/主数据/税务等），调用 `/api/common/getMenuAuth` 与 `/api/overView/overView`（`biz_code=100000`）。
   - **`haoma.lianjia.com`（号码通 / 免打扰电话查询，`authenticated_page`）**：CAS 签发 `helper_lianjia_com`，直出 `号码通` 主页面。

4. **激励/物料/内容设计与其他运营系统**：
   - **`b.ke.com`（币平台，`authenticated_verified`）**：侧栏含 `我的账户`、`预算管理`、`查看详情`，展示 `贝壳币账户列表`，调用 `/web/account/currencies`、`/web/user/accountAssetCard`、`/web/user/searchAccountList`、`/web/user/searchCondition`（`biz_code=0`）。
   - **`limu.ke.com`（立木设计平台，`authenticated_verified`）**：侧栏含 `为你推荐`、`我的工作台`、`创建设计`、`模板中心`、`我的团队`、`智能工具`，调用 `/api/config/getApollo`、`/api/my/info`、`/api/public/recommend/*`、`/api/public/template/list2`、`/api/public/wxwork/qrcode`、`/api/team/list2`（`biz_code=0`）。
   - **`flint.lianjia.com`（火石作业物料，`authenticated_page`）**：顶栏含 `返回link首页`、`贝壳火石`、`作业物料`、`关于火石`，卡片含 `二手带看报告`、`换房方案`、`新房置业报告`、`房源信息纸`、`楼盘信息纸`，调用 `/api/flint/question` 与 `/flint/permission`。
   - **`content.lianjia.com`（国民链家后台 / 便民服务，`authenticated_verified`）**：终态 `/service/order/print`，调用 `content.lianjia.com/info` 完成主体校验。
   - **`mobile.lianjia.com`（LINK-APP 下载页，`authenticated_page`）**：静态展示页，无 CAS 跳转或 XHR。
   - **`hx.ke.com`（红星运营平台，`authenticated_page` 标注、实际落入 `/notauth`）**：CAS 登录后调用 `hx.ke.com/role/list`（`biz_code=100000`），因当前账号无运营角色跳往 `https://hx.ke.com/notauth`。

### 2.4 P0 权限拦截与连接异常分析

1. **安全网关拦截（4 站 `denied_403`）**：`e-contract.ke.com`（跳 `hip.ke.com/forbidden`）、`lingjing.ke.com`（跳 `hip.ke.com/forbidden`；而同标签的 `limu.ke.com` 可正常认证使用）、`pan.fang.lianjia.com`（跳 `hip.lianjia.com/forbidden`）、`newcms.fang.lianjia.com`（跳 `hip.lianjia.com/forbidden`）。
2. **业务 RBAC 权限拦截（4 站 `denied_403`）**：
   - `oomp.ke.com`（与 `naturaloppty.ke.com` 一致，调用 `/api/privilege/allRole` 后落入无权态）；
   - `price.lianjia.com`（`/fee/getBaseInfo`、`/fee/getUserSupportedCities` 返回 `biz_code=-99990`，显示 `Access 无访问权限`）；
   - `morg.lianjia.com`（CAS 后跳至 `/error/noPermission`）；
   - `wi.ke.com`（CAS 签发 `g_wi_token`，页面渲染 `无权限` 状态）。
3. **传输与网络不可达（3 站 `error`）**：
   - `yezhu.link.lianjia.com`：`net::ERR_CONNECTION_CLOSED`；
   - `ncrm.lianjia.com`：`net::ERR_SOCKS_CONNECTION_FAILED`；
   - `pref.merlin.lianjia.com`：`net::ERR_SOCKS_CONNECTION_FAILED`。

---

## 第三部分：衔接 Phase 2B-P1P2（72 站）准备状态与耗时估算

1. **目标清单就绪情况**：
   - [phase2b_p1p2_screen.json](./phase2b_p1p2_screen.json) 与 [phase2b_p1p2_screen.md](./phase2b_p1p2_screen.md) 已完成 89 站 P1/P2 轻量快筛，明确筛出 **72 站** 进入下一轮浏览器深扫（**P1 41 站 + P2 31 站**，其中 `accessible_verified_or_page` 47 站、`spa_shell_needs_browser` 25 站；已排除 3 个 `denied_403`、7 个 `login_required`、7 个 `unreachable_or_error`）。
   - 经校验，这 72 个 P1/P2 目标主机与本轮已落盘 [site_profiles_2b.jsonl](./site_profiles_2b.jsonl) 的 72 站（`2B-1` 33 站 + `2B-P0` 39 站）**零重叠**。
   - 下一步只需从 `phase2b_p1p2_screen.json`（`enter_2b2_hosts`）过滤 `phase2b_p1p2_targets.json` 生成 72 站清单，直接复用 `scripts/phase2_site_profiler.py --use-tunnel --resume --timeout-ms 20000` 追加写入 `site_profiles_2b.jsonl`（完成后总计将达到 `144` 行；注意因 `load_resume_records` 会过滤历史 `error` 记录，执行 `--resume` 前需留意保留已确认的 4 条 `error` 档案或先导出 P1/P2 独立 JSONL 再合并）。
2. **耗时与稳定性估算**：
   - 基于本轮 72 站真实深扫统计（总耗时 `790.61s`，均值 **`10.98s`/站**、中位数 **`10.74s`/站**，单站硬超时 `20s`）：
   - 预计下一轮 **72 站 P1/P2 浏览器深扫总耗时约 13–16 分钟**（建议控制在单次任务 20 分钟超时阈值内，或按 P1 41 站 ≈ 7.5 分钟 / P2 31 站 ≈ 5.5 分钟两段串行合并）。
   - **隧道稳定性提示**：本轮在单站错误触发高频短间隔重建 SSH 隧道时观测到一次远端 `kex_exchange_identification: Connection reset`（`code=255`），间隔约 35 秒后即恢复正常；因此下一轮若分两批执行或触发隧道重建，建议预留短冷却间隔避免连续触发远端 SSH 端口建连重置。

---

## 第四部分：脱敏自检、进程释放与隔离确认

- **离线脱敏门禁**：全工作区扫描结果将写入 [phase2b_p0_redaction_check.json](./phase2b_p0_redaction_check.json)，确保 `passed == true` 且 `findings == []`。
- **进程与隔离确认**：[phase2_site_profiler.py](./scripts/phase2_site_profiler.py) 退出时已输出 `tunnel_cleanup: {"started": true, "terminated": true, "port_released": true, "return_code": 1}`，Playwright 与临时 SOCKS5 隧道均已释放，系统既有 `8020` 隧道保持原样未受干扰，未改动工作目录外任何文件。
