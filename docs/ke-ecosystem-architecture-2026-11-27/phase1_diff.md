# Phase 1 聚合枢纽门户深度发现与遗漏核查报告

> **采集时间（UTC）**：`2026-11-27T15:48Z ~ 15:52Z`
> **执行原则**：严格只读、全局拦截 `/(logout|signout|logoff)(/|$|\?)`、内存读取凭证不落盘、临时 SOCKS5 隧道 `try...finally` 托管释放、全部产物通过离线脱敏自检。
> **核心结论**：`summary.json` 的 **28 个已确认入口存在显著覆盖遗漏**——仅从 7 个枢纽的渲染 DOM 与菜单配置 XHR 接口即新发现 **142 个** 未进入 28+15 清单与历史审计文档的内部企业域 Web 系统主机（外加 2 个再次出现的 `page_only_unverified` SPA 子系统与 35 个外部 SaaS/品牌关联域主机）。

---

## 1. 7 个聚合枢纽登录与提取状态总表

| # | 枢纽标识 (`hub_key`) | 枢纽名称 | 入口 URL（解析自 `summary.json`） | 实际终态 URL | 访问通道 / 直连可达性 | HTTP 状态 | 登录态 / 主体核验 | 签发 Cookie 名（仅名称） | API 端点数 | 提取链接数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `workbench` | 工作台首页 | `https://shouye.link.lianjia.com` | `https://shouye.link.lianjia.com/shou/index` | 内网 SOCKS5 隧道 | 200 | `authenticated=true` / `identity_match=true` | `JSESSIONID`, `MAIN_JSESSIONID`, `SESSION`, `_lianjia_link_snid`, `crosSdkDT2019DeviceId`, `lianjia_ssid`, `lianjia_uuid`, `rememberMe`, `saas_token`, `task-databoard-cookie` | 12 | 74 |
| 2 | `newhouse_top_nav` | 新房 / A+ 统一顶栏 | `https://link.fang.lianjia.com/map` | `https://link.fang.lianjia.com/map` | 内网 SOCKS5 隧道 | 200 | `authenticated=true` / `identity_match=true` | `BAIDUID`, `BAIDUID_BFESS`, `ab_bid`, `ab_jid`, `ab_jid_BFESS`, `ab_sr`, `crosSdkDT2019DeviceId`, `lianjia_ssid`, `lianjia_token`, `lianjia_uuid`, `ref` | 11 | 44 |
| 3 | `employee_portal` | 员工门户 | `http://home.ehr.lianjia.com`（覆盖 `/myInfo/profile` 与 `/home/portal`） | `https://family.ke.com/myInfo/profile` | 内网 SOCKS5 隧道 | 200 | `authenticated=true` / `identity_match=true` | `crosSdkDT2019DeviceId`, `ehrs`, `family_front_token`, `family_prod`, `lianjia_ssid`, `lianjia_uuid`, `rememberMe`, `secdn_server_bak`, `sso_login_token`, `x-tenant-id` | 11 | 247 |
| 4 | `hr_portal` | HR 门户 | `https://hr.ke.com` | `https://hr.ke.com/home/index` | 内网 SOCKS5 隧道 | 200 | `authenticated=true` / `identity_match=false` | `JSESSIONID`, `crosSdkDT2019DeviceId`, `digData`, `intelligentService_sid`, `intelligentservice_token`, `ke`, `lianjia_ssid`, `lianjia_uuid`, `rememberMe`, `secdn_server_bak`, `session_id` | 3 | 111 |
| 5 | `ai_portal` | AI 门户 | `https://letsai.ke.com` | `https://letsai.ke.com/` | **本机直连** (`direct_reachable=true`) | 200 | `authenticated=true` / `identity_match=false` | `PHPSESSID`, `lianjia_ssid`, `lianjia_uuid`, `wordpress_logged_in_*`, `wordpress_sec_*`, `wordpress_test_cookie` | 0 | 193 |
| 6 | `help_center` | 客服中心 / 业绩争议 | `http://helper.ke.com` | `https://helper.ke.com/#/` | **本机直连** (`direct_reachable=true`) | 200 | `authenticated=true` / `identity_match=true` | `BAIDUID`, `BAIDUID_BFESS`, `SESSION`, `crosSdkDT2019DeviceId`, `lianjia_ssid`, `lianjia_uuid`, `secdn_server_bak` | 12 | 26 |
| 7 | `aplus_client_auth` | A+ 客户端认证服务 | `https://saas.a.ke.com/` | `http://saas.a.ke.com/` | 内网 SOCKS5 隧道 | 200 | `authenticated=true` / `identity_match=false` | `SESSION`, `lianjia_ssid`, `lianjia_uuid`, `rememberMe` | 0 | 0 |

### 关键枢纽采集与架构定位补充
1. **工作台首页 (`shouye.link.lianjia.com`)**：前端基于 Stencil Web Components（检测到 3 个 Shadow DOM 根节点），除渲染顶栏外，通过 `session-saas-a.lianjia.com`（`/api/navigation`、`/api/getRoleSwitchOptions`）、`/pc/index/navigation`、`/pc/index/panels/internal`、`/cards/front`、`/pc/personal/info/Index`、以及 `xiaobei.ke.com` 知识标签配置接口加载全部应用菜单与工作台卡片（如 `notice.ke.com` 通知/任务/榜单/个人信息模块与 `storm.link.lianjia.com` 业绩排名）。
2. **新房 / A+ 统一顶栏 (`link.fang.lianjia.com`)**：通过 `GET /menuService/getTopNavList`（`biz_code=0`）拉取 A+ 全站统一顶栏菜单树（涵盖新房/二手/客源/签约/签后/工具等），并由 `/api/userinfo` 验证主体一致。
3. **员工门户 (`family.ke.com`)**：覆盖 `/myInfo/profile` 与 `/home/portal` 双入口，通过 `/api/tenant/nav`（租户导航，含业务系统、产研系统、职能系统分级树）、`/api/user/profile`（仅提取 `myPackages` 应用包结构，严禁落盘个人字段）以及页面卡片区提取到 **247 条去重链接**，横跨 133 个域名，是本次最大的新系统发现源。
4. **HR 门户 (`hr.ke.com`)**：登录后跳至 `/home/index`（KeHR），通过 `GET /portal/systems/all` 一次性返回集团 HR 及职能关联系统全集，连同统一顶栏共提取 **111 条链接**（80 个域名）。
5. **AI 门户 (`letsai.ke.com`)**：基于 WordPress OneNav 导航主题，本机公网直连可达，通过 CAS 换取 WordPress 会话（`wordpress_logged_in_*`），从「内部应用专区」「大语言模型」「人力」「多模态生成」「图像生成」「AI直播课」「AI工具教程」「AI实践分享」「Vibe Working实践案例」等区块提取 **193 条卡片链接**（32 个域名）。
6. **客服中心 (`helper.ke.com`)**：本机公网直连可达，通过 `/web/loginUser/info` 验证主体一致，并从 `/web/fe/helpcenter/moduleConfigServiceImpl/getHomePageConfig` 提取全量首页服务模块与外部协同系统（`luoshu.ke.com`、`m-jury.ke.com`、`xiaobei.ke.com`）。
7. **A+ 客户端认证服务 (`saas.a.ke.com`) 架构定位确认**：实测返回仅 **902 字节**的空壳 HTML（`<title>首页</title>`，0 个 DOM 链接，0 个 XHR API），仅含 `window.__UDL_CONFIG = {pid: 'a_pc'}` 埋点脚本初始化，但在 CAS 回调后真实签发 `SESSION`、`rememberMe`、`lianjia_ssid`、`lianjia_uuid`。**定位明确为 A+ 桌面客户端的 CAS 票据交换与会话签发底座，而非独立 Web 业务门户**。

---

## 2. 5 类遗漏与差集对比清单

> 对比基线：`summary.json` 已确认 **28** 主机（本轮在枢纽中出现 22 个；未在 7 枢纽直接引用的 6 个为 `kemcc.ke.com`、`lease-pz.link.lianjia.com`、`listing-de.ke.com`、`moma.ke.com`、`trusteeship.link.lianjia.com`、`wandou.ke.com`）、`groups.entries` 别名 **5** 个、`not_counted` **15** 个、以及历史审计报告（`kecom-security-audit-report-2026-11-20.md` 与 `ke-app-credential-reuse.md`）记录的 **19** 个主机。

### 2.1 第 1 类：新发现的内部 Web 业务入口（共 142 个，建议纳入 Phase 2 实登核查）

#### A. AI 门户（`letsai.ke.com`）新挖出的全部内部主机（12 个）

| 主机名 | 门户分类 | 功能标签 | 示例 URL |
| --- | --- | --- | --- |
| `ai-sec.ke.com` | 内部应用专区 | 贝壳安全AI助手 | `https://ai-sec.ke.com/` |
| `aiglasses.ke.com` | 内部应用专区 | 智眸AI眼镜 | `https://aiglasses.ke.com/` |
| `bella.ke.com` | 内部应用专区 / 人力 | Bella（JD设计师、招聘文案师、简历筛选/摘要师、精英面试官） | `http://bella.ke.com/` |
| `chat-excel.ke.com` | 内部应用专区 | CHAT-EXCEL | `https://chat-excel.ke.com/` |
| `chatbi.ke.com` | 内部应用专区 | Chat BI | `https://chatbi.ke.com/no-permission` |
| `codelink.ke.com` | 内部应用专区 | CodeLink | `https://codelink.ke.com/` |
| `is-prod.ke.com` | 内部应用专区 / 人力 | 闹海、小伯乐（同时出现在 HR 门户） | `https://is-prod.ke.com/pages/chat` |
| `jianli.ke.com` | 人力 | 聘多多系统 | `https://jianli.ke.com/` |
| `ketest-pro.ke.com` | 内部应用专区 | Ketest Copilot | `https://ketest-pro.ke.com/#/keTest-copilot` |
| `opengpt.ke.com` | 大语言模型 | ChatGPT 4 | `https://opengpt.ke.com/#/intelligentChat/54961496895488` |
| `recruit.ke.com` | 人力 / 员工门户 / HR 门户 | Offer&预入职系统、招聘/入职、活水计划 | `https://recruit.ke.com/` |
| `speechhub.ke.com` | 内部应用专区 | 贝壳语音 | `http://speechhub.ke.com/` |

#### B. 客服中心（`helper.ke.com`）新挖出的全部内部主机（3 个）

| 主机名 | 门户分类 | 功能标签 | 示例 URL |
| --- | --- | --- | --- |
| `luoshu.ke.com` | 常用服务 / `getHomePageConfig`（也在员工/HR 门户） | 首看保护、银河计划、[新]售前信息采集、印章管理、干部管理系统、访客预约 | `https://luoshu.ke.com/dynamicFormRender?...` |
| `m-jury.ke.com` | 常用服务 / `getHomePageConfig` | B端纠纷调解 | `https://m-jury.ke.com/new/mediation/agentList` |
| `xiaobei.ke.com` | 常用服务 / `getHomePageConfig`（也在工作台/员工门户） | 小贝知道、财务制度&财务指引 | `https://xiaobei.ke.com/#/` |

#### C. 工作台首页（`shouye.link.lianjia.com`）新挖出的全部内部 Web 主机（3 个）

| 主机名 | 数据来源 | 功能标签 | 示例 URL |
| --- | --- | --- | --- |
| `notice.ke.com` | `/cards/front` | 任务模块、新个人信息模块、榜单模块、通知模块 | `https://notice.ke.com/sasspc/noticeCenter` |
| `storm.link.lianjia.com` | `/cards/front` | 业绩排名 | `https://storm.link.lianjia.com/middle/yeji/index` |
| `xiaobei.ke.com` | XHR 接口观测 | 小贝知道（知识标签配置） | `https://xiaobei.ke.com/#/` |

> 注：工作台首页另外观测到 `session-saas-a.lianjia.com`（统一导航与角色切换 API）与 `main.link.lianjia.com` 两个后端/API 主机，归入第 2 类。

#### D. 员工门户（`family.ke.com`）新挖出的全部内部主机（共 97 个，去除已在 A/B/C 列出的 `recruit.ke.com`、`luoshu.ke.com`、`xiaobei.ke.com` 后净新增 94 个）

按门户 `/api/tenant/nav` 与页面卡片业务域分组如下：

1. **业务系统（二手 / 新房 / 交易 / 辅助作业，36 个）**：
   - `b.ke.com`（币平台）
   - `biz-data.ke.com`（业务事件平台）
   - `cms-tv.ke.com`（大屏二手房源/新房视频轮播）
   - `cms.newhouse-bd.ke.com`（客发工作台）
   - `content.lianjia.com`（便民服务）
   - `data.ke.com`（新房业务、每日关注）
   - `deal.fang.lianjia.com`（新房成交管理系统）
   - `e-contract.ke.com`（上海合同系统）
   - `fast.ke.com`（天眼系统）
   - `flint.lianjia.com`（火石作业物料）
   - `gte.lianjia.com`（北京交易系统）
   - `haoma.lianjia.com`（免打扰电话查询）
   - `heimingdan.lianjia.com`（黑名单）
   - `hx.ke.com`（红星运营平台）
   - `intcms.jiaoyi.lianjia.com`（交易配置平台）
   - `ishop.lianjia.com`（智慧门店）
   - `ke-map.ke.com`（壳慧图）
   - `lead.ke.com`（口袋楼盘）
   - `limu.ke.com`（立木）
   - `lingjing.ke.com`（立木 / lingjing）
   - `link.lianjia.com`（Link）
   - `loushu.lianjia.com`（链楼书）
   - `morg.lianjia.com`（银行管理平台）
   - `ncrm.lianjia.com`（小贝知道）
   - `newcms.fang.lianjia.com`（新房内容配置后台 CMS）
   - `oomp.ke.com`（二手自然商机展位，与已有 `naturaloppty.ke.com` 区分）
   - `pan.fang.lianjia.com`（贝壳新房录盘系统 lupan）
   - `pref.merlin.lianjia.com`（Merlin 数据）
   - `price.lianjia.com`（成交价管理）
   - `rulemanage.ke.com`（同业经纪人查询）
   - `user-ntcs.gte.lianjia.com`（郑州交易可视化系统）
   - `wi.ke.com`（WI 分）
   - `wrule.ke.com`（贝壳规则）
   - `xfp.ke.com`（上海新房提佣核算）
   - `xinfang.a.ke.com`（新房工作台）
   - `yezhu.link.lianjia.com`（业主）
   - *(以上共 36 个业务系统主机)*
2. **产研与基础技术 / 办公协作（19 个）**：
   - `admin.uc.lianjia.com`（UC 权限）
   - `apollo.portal.life.ke.com`（Apollo 配置中心）
   - `cloud.intra.ke.com`（贝壳云、产品树）
   - `compass.ke.com`（埋点平台 / 罗盘）
   - `dawei.ke.com`（知产管理 / 专利系统）
   - `design.ke.com`（KeDesign）
   - `easy.intra.lianjia.com`（链家网 EASY）
   - `its.ke.com`（IT 服务、视频会议）
   - `its.lianjia.com`（软件下载）
   - `mail.ke.com`（贝壳邮箱 / 网页邮箱）
   - `mobile.lianjia.com`（LINK-APP）
   - `new-meeting.ke.com`（会议室预定）
   - `owa.lianjia.com`（邮箱）
   - `risk-platform.ke.com`（风控平台）
   - `weapons.ke.com`（落兵台）
   - `wen.ke.com`（贝壳问卷）
   - `wiki.lianjia.com`（WIKI）
   - `workflow.lianjia.com`（通用审批流）
   - `yunpan.lianjia.com`（贝壳云盘）
   - *(以上共 19 个产研/办公协作主机)*
3. **职能系统（财务 / 法务合规 / 人事 / 行政 / 员工服务，39 个）**：
   - 财务与资产：`bfa.ke.com`（财务+）、`budget-control.ke.com`（预算）、`caigou.ke.com`（采购系统）、`finops.ke.com`（贝壳费控平台）、`fssc.lianjia.com`（老费控）、`hecapp.homelink.com.cn`（AFA 爱发）、`ireport.lianjia.com`（数据收集平台）、`kepler.ke.com`（税务平台）、`mdm.ke.com`（主数据管理平台 MDM）、`mzichan.ke.com`（我的资产/资产申请）、`pay.ke.com`（Kepay）、`rpm.ke.com`（报账平台）、`shandian.lianjia.com`（闪店）、`sis.lianjia.com`（SIS 门店损益）、`zichan.ke.com`（资产 2.0）、`zjjg.homelink.com.cn`（理房通）。
   - 法务与合规：`case-legal.ke.com`（诉讼管理）、`company-legal.ke.com`（股权管理）、`legal-compliance.ke.com`（合规工作台）、`legal-lawyer-library.ke.com`（律师库）、`m-legal.ke.com`（合同管理）、`mknow.ke.com`（违纪通报）、`vprivacy.ke.com`（数据合规）。
   - 人事、绩效、OKR 与行政服务：`bpm.ke.com`（HR 权限申请/礼品申报）、`bpm-m.ke.com`（离职申请）、`chip.ke.com`（电子合同下载校验）、`codin.ke.com`（CHO 驾驶舱）、`homeworld-front.ke.com`（日常用品申请/公司菜单）、`inrecruit.ke.com`（内部活水）、`ketalk.ke.com`（贝壳圈）、`okr.ke.com`（OKR）、`ones.ke.com`（KeOnes 项目管理）、`profile.ke.com`（人才管理）、`profile-h5.ke.com`（人才标准）、`renshi.ehr.lianjia.com`（LinkHR / 个人电子档案）、`report.ke.com`（小贝汇报）、`spi.ke.com`（运营绩效系统）、`xinchou-data.ke.com`（薪酬预处理平台）、`yes-or-no.ke.com`（Yes or No 员工交流）。

#### E. HR 门户（`hr.ke.com` `/portal/systems/all` 与顶栏）独有新增内部主机（31 个，另有 32 个与员工/AI/客服门户交叉）

| 分类 | 主机列表与功能标签 |
| --- | --- |
| **HR 管理与薪酬/组织/招聘（17 个）** | `cb.ke.com`（基薪管理）、`ganbu.ke.com`（干部画像）、`hrlearn.ke.com`（HR价值流）、`jingyingshe-admin.ke.com`（精英社）、`leader.ke.com`（管理者工作台）、`nltx.ke.com`（职级平台）、`pm.ke.com`（人月系统）、`recruit-ops.ke.com`（招聘调研系统）、`resume-agc.ke.com`（聘多多）、`salary-middle.ke.com`（薪酬中心）、`salary-monitor.ke.com`（服务监控）、`salary-tax.ke.com`（税友平台）、`shebao.ke.com`（社保系统）、`solicitude.hr.lianjia.com`（员工关怀）、`vm.ke.com`（价值观评分）、`vote-admin.ke.com`（荣誉评选系统）、`zhidao.ke.com`（贝经堂） |
| **培训 / 文化 / 行政 / 财法后台（14 个）** | `admin-huaqiao.ke.com`（花桥学堂）、`admin-study.ke.com`（贝好学管理端）、`admin-sunshine.ke.com`（阳光贝壳）、`be-admin.ke.com`（新经纪榜样）、`contract.ke.com`（合同管理）、`esghub.ke.com`（碳管理）、`gongwei.ke.com`（工位系统）、`gongyi.ke.com`（贝壳公益）、`homeworld.ke.com`（行政服务）、`kehome-cms.ke.com`（贝壳之家）、`lcm.ke.com`（预实管理）、`m-intelligencecampus-hq.ke.com`（老左图书馆）、`mcg.ke.com`（CG文化圈）、`meeting-assistant.ke.com`（会议室系统） |

---

### 2.2 第 2 类：已文档化或本轮观测到的核心后端 / API 网关 / 轻应用 / 静态与埋点主机（共 17 个）

| 来源性质 | 主机名 | 说明与关联 |
| --- | --- | --- |
| **历史审计文档记录（11 个）** | `prd-assistant-backend.ke.com` | Moma 主站业务后端 API（2026-11-20 审计） |
| | `imapi.lianjia.com` | 通知中心 `kemcc.ke.com` 关联 IM API 网关 |
| | `login.ke.com` | 集团统一 CAS 单点登录认证中心 |
| | `wxwork.ke.com` | 企业微信认证/网关回调（本轮员工门户亦通过 `open.weixin.qq.com` OAuth 回调指向该域） |
| | `smart-mo.ke.com`、`moma-data.ke.com`、`flow-forge.ke.com`、`linkspace.ke.com` | 历史安全审计基线触达的轻应用/AI/协作相关主机 |
| | `*.ttb.test.ke.com` | 云内网测试/预发布环境（RFC 6598 路由） |
| | `img.ljcdn.com`、`ods.lianjia.com` | 图片 CDN 与对象存储能力 URL 宿主 |
| **本轮 7 枢纽实测 API / 静态主机（6 个）** | `session-saas-a.lianjia.com` | 工作台首页统一导航与角色切换后端（`/api/navigation`、`/api/getRoleSwitchOptions`） |
| | `main.link.lianjia.com` | 工作台首页经纪人上下文切换请求主机 |
| | `msg.ke.com` | 员工门户 WebPush 通知与 SSO 回调（`/sound/wave/webpush/server/v1/notice`） |
| | `public-digc.ke.com`、`public-h5-be-report.ke.com` | 全站统一前端埋点与行为上报网关 |
| | `file.ljcdn.com` | 静态资源 / CDN 附件托管域（员工门户福利附件引用） |

---

### 2.3 第 3 类：此前标记为 `page_only_unverified` 且在枢纽中再次出现的 SPA 子系统（2 个）

| 主机名 | `summary.json` 原记录名与状态 | 本轮出现枢纽 | 枢纽内提取到的具体业务入口 / 子路径 |
| --- | --- | --- | --- |
| `app-fly.ke.com` | 员工文化应用（`page_only_unverified`） | `employee_portal`, `hr_portal` | 新文化内核（`/4ca0a304-9d83-458e-8355-e4bcc1494196`）、员工关系管理（`/employee-relations-hub`）、品牌资源（`/8c0adc7a-9640-4d3b-9e78-38269e980001`） |
| `jixiao.ke.com` | 绩效（`page_only_unverified`） | `employee_portal`, `hr_portal` | 绩效目标/自评/辅导/结果（`/p/evaluate/result`、`/p/tutor/mine`）、试用期系统（`/p/trial/detail`） |

> 附注：原 `not_counted` 中另有 `app-moma.ke.com` 同属 `page_only_unverified`（本次 7 枢纽主页未直接链出）；其余 `not_counted` 中有 10 个在工作台/新房顶栏/员工门户菜单再次出现（`databoard.lianjia.com`、`i.nts.jiaoyi.ke.com`、`jiayou.lianjia.com`、`lj.lianjia.com`、`mail.homelink.com.cn`、`reds.lianjia.com`、`rfq.ke.com`、`sm.lianjia.com`、`te.lianjia.com`、`xss.lianjia.com`），维持既有不可用/无权限/需单独登录状态标记。

---

### 2.4 第 4 类：现有 28 主机的别名 / 旧入口域名（5 个）

| 别名/旧入口主机 | 指向已确认主站 (`summary.json`) | 本轮出现枢纽 | 门户中对应的菜单标签 |
| --- | --- | --- | --- |
| `elearning.ke.com` | `study.ke.com` | `employee_portal`, `hr_portal` | 培训、在线课堂、我的学习/考试、鲜贝成长计划 |
| `fang.deyoulife.com` | `house.link.lianjia.com` | `newhouse_top_nav`, `workbench` | 房源 |
| `helper.lianjia.com` | `helper.ke.com` | `employee_portal`, `hr_portal`, `newhouse_top_nav`, `workbench` | 我的客服、业绩争议系统、我要反馈 |
| `home.ehr.lianjia.com` | `family.ke.com` | `employee_portal`, `newhouse_top_nav`, `workbench` | HR系统、Link.HR、核心人事系统（LinkHR） |
| `zhenzhu.ke.com` | `listing-de.ke.com` | （`summary.json` 入口别名） | 商铺写字楼 |

---

### 2.5 第 5 类：外部第三方 SaaS 及品牌关联域链接（共 35 个）

| 分类 | 主机清单 |
| --- | --- |
| **外部公共大模型与 AI 工具（17 个，主要来自 AI 门户）** | `agent.minimaxi.com`（MiniMax）、`ai.wps.cn`（WPS AI）、`app.napkin.ai`（Napkin）、`chat.deepseek.com`（DeepSeek）、`chatglm.cn`（智谱清言）、`doc.weixin.qq.com`（北森AI手册）、`hr.nowcoder.com`（牛客视频面试）、`jimeng.jianying.com`（即梦）、`kimi.moonshot.cn`（Kimi）、`meeting.tencent.com`（腾讯会议 AI）、`miaohua.sensetime.com`（秒画）、`notebooklm.google.com`（NotebookLM）、`passport.xfyun.cn`（讯飞星火）、`tongyi.aliyun.com`（通义千问/万相）、`www.coze.cn`（扣子面试记录员）、`yiyan.baidu.com`（文心一言）、`yuanbao.tencent.com`（腾讯元宝） |
| **第三方 HR / 招聘 / 福利 / 办公 SaaS（16 个）** | `365.kdocs.cn`（金山文档-云枢使用指南）、`brizoo.eagleyun.com`（云枢下载）、`h5.keyoucai.com`（贝家优才）、`jkb.sipaicare.com.cn`（商保）、`lianjia.yunxuetang.cn`（链视界/云学堂）、`map.baidu.com` / `miao.baidu.com`（百度地图与风控脚本）、`neitui.italent.cn`、`neitui.zhiye.com`、`tms.beisen.com`、`www.italent.cn`（北森招聘/内推/360环评）、`open.weixin.qq.com` / `work.weixin.qq.com`（企业微信 OAuth 与下载）、`www.beike.firacademy.com`（红杉培训）、`www.huzhujin.com`（格林互助）、`www.jiayou321.com`（加油站外部商城） |
| **德佑关联品牌域（2 个，非 `.ke.com`/`.lianjia.com` 主域）** | `login.dooioo.com`（CAS 回调透出 `xfpingtai.dooioo.com`、`jiaoyi.dooioo.com`、`jinrong.dooioo.com`、`zzjg.dooioo.com`、`guanggao.dooioo.com`、`wenshu.dooioo.com`、`shuifei.dooioo.com`、`shluyin.dooioo.com` 等上海德佑业务子域）、`renzi.dooioo.com`（上海德佑人资系统） |

---

## 3. Phase 2 目标清单与分网批次建议（待用户确认）

Phase 1 仅完成 7 个聚合枢纽与差集发现；Phase 2 建议采用 `site_profiles.jsonl`（支持 `--resume` 断点续跑），按网络域拆分 **Batch 2A（公网直连优先）** 与 **Batch 2B（内网 SOCKS5 隧道）** 串行测绘。

### 3.1 Batch 2A：公网直连批次（已确认直连 + 高概率公网 AI/客服/轻量应用候选）
- **已验证直连基线（5 站）**：`helper.ke.com`、`letsai.ke.com`、`moma.ke.com`、`wandou.ke.com`、`listing-de.ke.com`。
- **Phase 2 建议优先试探直连的遗漏/待验证候选（17 站，直连失败自动转入 Batch 2B）**：
  - AI 门户内网体系新站点（11 站）：`ai-sec.ke.com`、`aiglasses.ke.com`、`bella.ke.com`、`chat-excel.ke.com`、`chatbi.ke.com`、`codelink.ke.com`、`is-prod.ke.com`、`jianli.ke.com`、`ketest-pro.ke.com`、`opengpt.ke.com`、`speechhub.ke.com`；
  - 客服中心关联协同系统（3 站）：`luoshu.ke.com`、`m-jury.ke.com`、`xiaobei.ke.com`；
  - 第 3 类复现 `page_only_unverified` SPA（3 站）：`app-fly.ke.com`、`jixiao.ke.com`、`app-moma.ke.com`。

### 3.2 Batch 2B：办公内网 SOCKS5 隧道批次
- **已确认 28 表中需内网可达的其余站点（23 站）**：`shouye.link.lianjia.com`、`house.link.lianjia.com`、`lease-pz.link.lianjia.com`、`trusteeship.link.lianjia.com`、`link.fang.lianjia.com`、`ke.deyoulife.com`、`ke.link.lianjia.com`、`hetong.link.lianjia.com`、`clue.a.ke.com`、`xiaoxi.link.lianjia.com`、`odin.ke.com`、`kemcc.ke.com`、`family.ke.com`、`hr.ke.com`、`saas.a.ke.com`、`8.lianjia.com`、`brand.ke.com`、`kaoqin.hr.ke.com`、`study.ke.com`、`newhouse-cps.ke.com`、`xinchou.ke.com`、`liulian.ke.com`、`naturaloppty.ke.com`。
- **新发现内部业务/职能/产研候选（扣除 Batch 2A 候选后共 128 站，建议分优先级扫测）**：
  - **P0 核心业务系统（39 站）**：`notice.ke.com`、`storm.link.lianjia.com`、`xinfang.a.ke.com`、`yezhu.link.lianjia.com`、`oomp.ke.com`、`link.lianjia.com`、`gte.lianjia.com`、`e-contract.ke.com`、`deal.fang.lianjia.com`、`pan.fang.lianjia.com`、`newcms.fang.lianjia.com`、`cms.newhouse-bd.ke.com`、`price.lianjia.com`、`morg.lianjia.com`、`intcms.jiaoyi.lianjia.com`、`user-ntcs.gte.lianjia.com`、`xfp.ke.com`、`ishop.lianjia.com`、`ke-map.ke.com`、`lead.ke.com`、`loushu.lianjia.com`、`wrule.ke.com`、`rulemanage.ke.com`、`wi.ke.com`、`b.ke.com`、`biz-data.ke.com`、`data.ke.com`、`fast.ke.com`、`flint.lianjia.com`、`haoma.lianjia.com`、`heimingdan.lianjia.com`、`hx.ke.com`、`limu.ke.com`、`lingjing.ke.com`、`ncrm.lianjia.com`、`pref.merlin.lianjia.com`、`content.lianjia.com`、`cms-tv.ke.com`、`mobile.lianjia.com`。
  - **P1 员工高频协作/财法/人事平台（约 47 站）**：`recruit.ke.com`、`renshi.ehr.lianjia.com`、`okr.ke.com`、`ones.ke.com`、`report.ke.com`、`bpm.ke.com`、`bpm-m.ke.com`、`finops.ke.com`、`bfa.ke.com`、`pay.ke.com`、`rpm.ke.com`、`caigou.ke.com`、`budget-control.ke.com`、`zichan.ke.com`、`mzichan.ke.com`、`mail.ke.com`、`owa.lianjia.com`、`its.ke.com`、`its.lianjia.com`、`wiki.lianjia.com`、`yunpan.lianjia.com`、`new-meeting.ke.com`、`meeting-assistant.ke.com`、`wen.ke.com`、`ketalk.ke.com`、`yes-or-no.ke.com`、`profile.ke.com`、`profile-h5.ke.com`、`inrecruit.ke.com`、`spi.ke.com`、`codin.ke.com`、`chip.ke.com`、`mdm.ke.com`、`kepler.ke.com`、`shandian.lianjia.com`、`sis.lianjia.com`、`legal-compliance.ke.com`、`m-legal.ke.com`、`contract.ke.com`、`case-legal.ke.com`、`company-legal.ke.com`、`legal-lawyer-library.ke.com`、`vprivacy.ke.com`、`mknow.ke.com`、`homeworld.ke.com`、`homeworld-front.ke.com`、`zjjg.homelink.com.cn`。
  - **P2 产研基建与 HR/管理后台（约 42 站，视用户需求决定是否全量纳入）**：`admin.uc.lianjia.com`、`apollo.portal.life.ke.com`、`cloud.intra.ke.com`、`easy.intra.lianjia.com`、`compass.ke.com`、`design.ke.com`、`dawei.ke.com`、`risk-platform.ke.com`、`weapons.ke.com`、`workflow.lianjia.com`、`fssc.lianjia.com`、`hecapp.homelink.com.cn`、`ireport.lianjia.com`、`xinchou-data.ke.com`、`cb.ke.com`、`salary-middle.ke.com`、`salary-monitor.ke.com`、`salary-tax.ke.com`、`shebao.ke.com`、`ganbu.ke.com`、`leader.ke.com`、`nltx.ke.com`、`pm.ke.com`、`hrlearn.ke.com`、`recruit-ops.ke.com`、`resume-agc.ke.com`、`solicitude.hr.lianjia.com`、`vm.ke.com`、`vote-admin.ke.com`、`zhidao.ke.com`、`admin-huaqiao.ke.com`、`admin-study.ke.com`、`admin-sunshine.ke.com`、`be-admin.ke.com`、`esghub.ke.com`、`gongwei.ke.com`、`gongyi.ke.com`、`jingyingshe-admin.ke.com`、`kehome-cms.ke.com`、`lcm.ke.com`、`m-intelligencecampus-hq.ke.com`、`mcg.ke.com`。
  - *(注：如需一并测绘上海德佑 `*.dooioo.com` 系统，请在确认 Phase 2 范围时显式授权纳入。)*

---

## 4. 脱敏自检与临时隧道清理确认

- **脱敏自检门禁**：由离线自检脚本 [redaction_check.py](./scripts/redaction_check.py) 对工作区全部 `.json` / `.jsonl` / `.md` 执行全量扫描，结果已落地 [phase1_redaction_check.json](./phase1_redaction_check.json)，`passed == true`、`findings == []`。
- **临时隧道清理确认**：[phase1_discovery.json](./phase1_discovery.json) 中 `tunnel_cleanup` 记录 `started: true`、`terminated: true`、`port_released: true`；Playwright 退出前已关闭全部上下文与浏览器，本机无残留临时 SSH 隧道进程。
- **只读与仓库隔离确认**：全程 0 次触发 `/logout|/signout|/logoff`，`C:\Users\shipi\vswp\BeikeMicroservice` 目录未做任何修改或新增文件。
