# 全量可用旁路系统功能拆解与接口能力手册（按七大业务域）

> **唯一数据基线**：本文严格依据 [final_ecosystem_dataset.json](./final_ecosystem_dataset.json) 与只读历史安全审计 [kecom-security-audit-report-2026-11-20.md](../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md) 编写，覆盖全生态浏览器深扫实证实质可用的全部 **91 个入口主机**（**A 级 38 站 + B 级 53 站**；按重定向归并去重后为 **89 个独立业务系统实体**），并逐域附注对应的 **15 个 C 级部分受限/空壳边界站点**。所有系统定位、认证 Cookie 族、UI 模块及后端 API 路径均源自离线实测记录，绝不臆测未观测接口或功能。

---

## 全局分域能力总览

| 业务域 | 深扫总量 | 实质可用（A+B） | 其中 A 级 / B 级 | 公网直连 / 内网独有 | C 级受限 | 不可用（D/E/F/G） | 核心枢纽与平台集群特征 |
| --- | ---: | ---: | :---: | :---: | ---: | ---: | --- |
| **1. 房源、新房与交易核心作业域** | 28 | **20** *(去重 19)* | 11 / 9 | 1 / 19 | 2 | 6 | A+ / Link / 德佑 Stencil 微前端，新房 `lianjia_token` + `darius.api` 配置中枢 |
| **2. 客源、线索、触达与协同通讯域** | 13 | **12** *(去重 11)* | 8 / 4 | 5 / 7 | 0 | 1 | 客源合流至 `ke.deyoulife`，公网集中客服/知识库/表单/IM/纠纷调解 |
| **3. 商业化展位、规则风控与运营工具域** | 17 | **11** | 7 / 4 | 0 / 11 | 1 | 5 | 展位报买/币平台、规则知识引擎、风控核验（含异常测试域外呼信号） |
| **4. AI 原生平台、数据智能与分析域** | 21 | **14** | 3 / 11 | 5 / 9 | 1 | 6 | Moma/Supabase 轻应用平台、Odin 大数据族、`is-prod` 智能服务核心 |
| **5. 员工门户、HR 人事、考勤薪酬与培训绩效域** | 30 | **16** | 4 / 12 | 0 / 16 | 1 | 13 | `family`/`hr` 门户枢纽、薪酬中台 3 站、`huaqiao_token` 学堂族 |
| **6. 财务费控、资产、法务合规与审批中台域** | 24 | **8** | 2 / 6 | 0 / 8 | 8 | 8 | `bfa` 7 大子平台、`budget-control`/`pay`/`kepler`、`contract` 与 `is-prod` 耦合 |
| **7. 产研基建、企业文化与行政服务域** | 23 | **10** | 3 / 7 | 0 / 10 | 2 | 11 | `apollo`/`cloud.intra`/`easy`/`compass`/`risk-platform`、IT 门户、CG 文化 |
| **全生态合计** | **156** | **91** *(去重 89)* | **38 / 53** | **11 / 80** | **15** | **50** | `cas_redirect` 84 站 + `none` 5 站 + `supabase_cas` 1 站 + `localstorage_bearer` 1 站 |

---

## 域 1：房源、新房与交易核心作业域（A+ / Link / 德佑 / 商办新版）

本域共有 **20 站**实质可用（A 级 11 + B 级 9；`link.lianjia.com` 与 `shouye.link.lianjia.com` 实体合流，去重后 **19 个独立系统**），另有 **2 站 Grade C**（`saas.a.ke.com`、`xfp.ke.com`）。核心由老链家 Link/签后交易系统、A+/德佑 Stencil Shadow DOM 微前端底座、新房 2.0 平台与商办新版 `listing-de.ke.com` 构成。

### 1.1 `shouye.link.lianjia.com`（工作台首页）[原28 · A级 · 内网独有]
- **系统定位与归并**：A+ / Link 经纪人统一工作台主入口（`title="链家网-Link-首页"`，终态 `https://shouye.link.lianjia.com/shou/index`）；枢纽标签 `待办`、`首页`；同属域内的 `link.lianjia.com`（P0 新增，A 级）经 CAS 回调后直接重定向合流至本页，二者共享全部会话、Shadow DOM 框架与请求链。
- **认证与 Cookie 族**：`cas_redirect`；签发 **A+ 底座簇** Cookie（`MAIN_JSESSIONID`、`_lianjia_link_snid`、`saas_token`），同时签发 **工作台任务簇** Cookie（`task-databoard-cookie`）；合流路径 `link.lianjia.com` 额外持有 `_se_semain_snid`、`link-lianjia`。
- **实测 UI 模块**：启用 **Stencil Shadow DOM** 微前端导航壳承载全局工作台卡片与待办面板。
- **核心后端 API**：
  - 本域接口：`GET shouye.link.lianjia.com/cas`（`302`）、`GET /cards/front`（`200/0`）、`GET /pc/index/navigation`（`200/0`）、`GET /pc/index/panels/internal`（`200/0`）、`GET /pc/personal/info/Index`（`200/0`，完成员工主体核验）。
  - 跨站能力底座：`GET session-saas-a.lianjia.com/api/navigation`、`GET /api/getRoleSwitchOptions`（`200/0`）；`GET main.link.lianjia.com/broker/change/[REDACTED]/`（`200`）；`GET xiaobei.ke.com/web/loginUser/checkUserLogin` 及 `knowledgeLabelServiceImpl/getKeyValueListFromConfig`（`200/1`）。
- **系统关联**：作为全生态核心门户枢纽之一，通过 `session-saas-a.lianjia.com` 与 `house`、`hetong`、`lease-pz` 共享导航与经纪人角色切换，并内嵌公网知识引擎 `xiaobei.ke.com`。

### 1.2 `link.lianjia.com`（Link 主域入口 — 重定向合流至 `shouye.link.lianjia.com`）[P0 · A级 · 内网独有]
- **系统定位**：原为老链家 Link 主域名，当前实测重定向合流至 `https://shouye.link.lianjia.com/shou/index`，与 `shouye.link.lianjia.com` 属于同一业务实体。
- **差异点与关联**：额外写入 `_se_semain_snid` 与 `link-lianjia` Cookie 后即切入 `shouye.link.lianjia.com` 全部 10 条 API 流水线。

### 1.3 `house.link.lianjia.com`（房源系统）[原28 · A级 · 内网独有 · 别名 `fang.deyoulife.com`]
- **系统定位**：核心二手房源作业主站（`title="德佑-房源"`，终态 `/search/sale/default/gdiv_mt`），枢纽入口标签覆盖 `房源`、`房源证件审核`、`钥匙管理`、`合同下载`、`参数配置`。
- **认证与 Cookie 族**：`cas_redirect`；签发完整 **A+ 底座五件套**（`MAIN_JSESSIONID`、`_lianjia_link_snid`、`saas_token`、`HOUSEJSESSIONID`、`login_ucid`）。
- **实测 UI 模块**：启用 **Stencil Shadow DOM** 微前端导航壳挂载房源列表检索、买卖/租赁切换与风控提示面板。
- **核心后端 API**：实测捕获 **12 个本域与底座接口**，包括 `GET house.link.lianjia.com/housedel/isGuidePrice`、`GET /layer/broker/abilityScoreOpen`、`POST /maintainwork/pc/housedel/smartside/apps/query`（`200/0`）、`GET /maintainwork/pc/housedel/suggestLingfengBroker`（`200/102`）、`GET /niceBroker/showV2`、`GET /pc/risk/getRiskInfoV3`、`GET /pouch/customer/isShowReminder`、`GET /promotion/banner`、`/cancelReason`、`/getPromotionBannerInfo`、`/screen`、`GET /remind/houseList`（除标明外均 `200/1`）。
- **系统关联**：与 `lease-pz.link.lianjia.com` 共享 `HOUSEJSESSIONID` 与 `login_ucid`；与 `shouye`/`hetong` 共享 A+ 顶栏和经纪人身份底座。

### 1.4 `hetong.link.lianjia.com`（签约合同系统）[原28 · A级 · 内网独有]
- **系统定位**：二手/租赁电子签约中心（`title="签约服务-vr"`，终态 `https://hetong.link.lianjia.com//index`），枢纽标签为 `签约`。
- **认证与 Cookie 族**：`cas_redirect`；签发 A+ 底座 `MAIN_JSESSIONID`、`_lianjia_link_snid` 与签约专用 `signapptob_token`。
- **实测 UI 模块**：启用 **Stencil Shadow DOM** 微前端壳承载签约菜单与经纪人角色切换器。
- **核心后端 API**：`GET hetong.link.lianjia.com/navigation/menuLinks`（`200/1`）；跨站调用 `GET main.link.lianjia.com/broker/change/[REDACTED]/`（`200`）与 `GET session-saas-a.lianjia.com/api/getRoleSwitchOptions`（`200/0`）。
- **系统关联**：直接消费 A+ 统一底座 `main.link.lianjia.com` 与 `session-saas-a.lianjia.com`。

### 1.5 `lease-pz.link.lianjia.com`（普租租赁系统）[原28 · B级 · 内网独有]
- **系统定位**：普租房源列表与作业工作台（终态 `https://lease-pz.link.lianjia.com/rent/house/list?isSaaS=false`）。
- **认证与 Cookie 族**：`cas_redirect`；全域最庞大的认证凭证聚合点之一，同时签发/承载 **16 个 Cookie**：A+ 底座（`MAIN_JSESSIONID`、`_lianjia_link_snid`、`HOUSEJSESSIONID`、`saas_token`、`login_ucid`）、老链家身份（`Lianjia_BUcid`、`Lianjia_curWorkCity`、`Lianjia_u_info`、`UCID`、`UCID_secure`）、租赁与托管核心令牌（`puzu_lease_token`、`puzu_lease_token_secure`、`lianjia_trusteeship_token`）、消息会话（`MESSAGEJSESSION`）及辅助字段（`autoLogout`、`csrfSecret`）。
- **实测 UI 模块**：普租房源搜索列表与二级标签筛选面板。
- **核心后端 API**：`POST lease-pz.link.lianjia.com/api/header/headerData`（`200`）、`GET /api/houseList/search/listSecondLevelTag`（`200`）、`POST /api/puzuHouse/puzu/house/auth/switchList`（`200`）；跨站 `GET main.link.lianjia.com/broker/detail/[REDACTED]/`（`200`）。
- **系统关联**：与 `trusteeship.link.lianjia.com` 共享 `lianjia_trusteeship_token`/`MESSAGEJSESSION`；根据历史审计（`KE-04`），本系统签发的 `puzu_lease_token` 可被公网 `wandou.ke.com` 作为 Bearer Token 接受。

### 1.6 `trusteeship.link.lianjia.com`（省心租托管业务系统）[原28 · A级 · 内网独有]
- **系统定位**：省心租 / 房屋托管全流程工作台（`title="首页"`，终态 `https://trusteeship.link.lianjia.com/home`）。
- **认证与 Cookie 族**：`none`（已承接内网浏览器 CAS 主干与托管会话 Cookie）；签发 `lianjia_trusteeship_token`、`MESSAGEJSESSION`、`trusteeship-front-state`。
- **实测 UI 模块**：顶栏含 `首页`、`客源`、`省心租工作台`、`线索`、`签后`、`人事服务`、`应用`、`消息`；侧栏含 `房源`、`合同`、`财务`、`业绩`、`客户管理`；核心卡片包括 `数据统计`、`收房 出房 在管`、`待办事项`、`审批流`。
- **核心后端 API**：实测捕获 **10 个业务接口**（全部 `200/100000`），包括 `/api/appeal/queryAppealPermissionPoint`、`/api/header/getWorkstationMenu`、`POST /api/header/headerData`、`/api/home/getNoticeConfig`、`/api/home/getOrg`、`/api/home/getStatisticsData`、`/api/home/getTodoData`、`/api/home/hasDataCenterPermission`、`/api/permission/checkPermBatchByRole`、`/api/saasSession/getRoleSwitchOptions`。
- **系统关联**：`lianjia_trusteeship_token` 同样被公网 `wandou.ke.com` 网关接收用于省心租 IM 会话交互（`KE-04`）；并与 `xiaoxi`/`lease-pz` 共享 `MESSAGEJSESSION`。

### 1.7 `link.fang.lianjia.com`（新房 / A+ 统一顶栏）[原28 · A级 · 内网独有]
- **系统定位**：新房楼盘地图查盘与 A+ 统一顶栏配置中枢（`title="新房"`，终态 `https://link.fang.lianjia.com/map`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 **新房 `lianjia_token` 共享簇** 与 `ref`。
- **实测 UI 模块**：新房楼盘地图筛选与统一顶部导航渲染。
- **核心后端 API**：实测 **8 个本域接口**（全 `200/0`），含 `/api/apollo/getConfig`、`/api/common/judgeCityOpen`、`/api/config/getGlobalConfig`、`/api/front/grayProject`、`/api/pc/cityList`、`/api/project/filterMapOption`、`POST /api/userinfo`、`/menuService/getTopNavList`。
- **系统关联**：与 `deal.fang`、`xinfang.a`、`loushu`、`ishop` 等 10 站共享 `lianjia_token` 簇。

### 1.8 `xinfang.a.ke.com`（新房工作台 2.0 Beta）[P0 · A级 · 内网独有]
- **系统定位**：新一代新房统一工作台微前端门户（`title="新房工作台"`，终态 `https://xinfang.a.ke.com/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `lianjia_token`、`login_redirect_url`、`xinfang_perms`、`xinfang_ucid`。
- **实测 UI 模块**：顶栏 `打印`、`注销`；主页欢迎卡片 `欢迎来到新房 2.0 Beta`、`服务于新房作业角色的工作台`。
- **核心后端 API**：本域 `POST xinfang.a.ke.com/api/menu`（`201`）、`GET /api/subapps`（`200`）；跨站共享配置网关 `POST darius.api.ke.com/apollo/config`（`201`）；跨站联调新房成交库 `GET deal.fang.lianjia.com/api/city/list`、`/api/common/redirectUrl`、`POST /api/utility/env`（均为 `200/0`）。
- **系统关联**：通过子应用注册机制 `api/subapps` 与 `deal.fang.lianjia.com` 形成双向互调（`deal.fang` 反向拉取 `xinfang.a.ke.com/api/currentServerConfig/publicReminder/list`）。

### 1.9 `deal.fang.lianjia.com`（新房成交管理系统）[P0 · A级 · 内网独有]
- **系统定位**：贝壳新房成交管理平台（`title="贝壳新房成交系统"`，终态 `https://deal.fang.lianjia.com/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `lianjia_token`、`deal_uc_id`、`login_redirect_url`。
- **实测 UI 模块**：顶栏 `退出`；侧栏 `新房成交系统`、`a.ke.com`；主页欢迎卡。
- **核心后端 API**：本域 `/api/city/list`、`/api/common/redirectUrl`、`/api/menu/list`、`POST /api/utility/env`、`/newapi/cityconfig/qianceIsopencity`（全 `200/0`）；跨站调用 `POST darius.api.ke.com/apollo/config`（`201`）与 `GET xinfang.a.ke.com/api/currentServerConfig/publicReminder/list`（`200`）。
- **系统关联**：与 `xinfang.a.ke.com` 互为微应用与服务提供方，共享新房共享网关 `darius.api.ke.com`。

### 1.10 `listing-de.ke.com`（商铺写字楼 / 商办新版）[原28 · A级 · 公网直连 · 别名 `zhenzhu.ke.com`]
- **系统定位**：商办物业（写字楼、商铺）房源与审核作业平台（`title="房源列表"`，终态 `https://listing-de.ke.com/pc/de/list`）。
- **认证与 Cookie 族**：`cas_redirect`；签发商办独立令牌族（`crep-listing-token`、`de_city_id`、`de_role_id`、`de_role_id_list`、`de_uc_id`）。
- **实测 UI 模块**：侧栏 `租赁房源`、`买卖房源`；核心卡片 `珍珠房源`、`录入房源`、`我的房源`、`我的举报`、`商办经纪人名单`、`实勘图`、`楼盘名称`、`业务类型`。
- **核心后端 API**：实测捕获 **12 个本域接口**（全 `200/100000`），涵括 `/api/pc/city/config/info`、`/api/pc/kemis/basic/building/historyRecords`、`/api/pc/kemis/dict/battlegroup`、`/dict/list`、`/dict/listForIndex`、`/pc/audit/todo`、`/pc/house/list`、`/pc/house/page/asset/hot_resblock/list`、`/pc/sug/buildingSug`、`/reportv2/page/reportee/task/proof/list`、`/api/pc/permission/role/list`、`/api/pc/truehouse/todo`。
- **系统关联**：作为域 1 唯一公网直连 A 级入口，独立使用 `crep-listing-token` 与 `kemis` 微服务体系。

### 1.11 `storm.link.lianjia.com`（签中中台 / 业绩排名）[P0 · A级 · 内网独有]
- **系统定位**：签中中台与月度业绩排名系统（`title="签中中台"`，终态 `https://storm.link.lianjia.com/middle/yeji/index`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `storm_token`。
- **实测 UI 模块**：聚焦签中业绩排名面板。
- **核心后端 API**：`GET /cashcow/betula/main/getIndexTabList`、`POST /cashcow/betula/main/queryMonthPerformanceRank`、`GET /cashcow/birch/leftTree/auth`（均 `200`）。
- **系统关联**：与 `gte.lianjia.com` 等签后系统共构交易中后台体系。

### 1.12 `lead.ke.com`（口袋楼盘）[P0 · A级 · 内网独有]
- **系统定位**：经纪人地理选盘与楼盘地图制作工具（`title="口袋楼盘"`，终态 `https://lead.ke.com/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `ke_lead_token`。
- **实测 UI 模块**：侧栏 `首页`、`自定义地图`、`门店商圈图`、`社区配套图`、`新房项目分布图`；卡片提示 `请使用微信或企微扫码分享`。
- **核心后端 API**：`GET lead.ke.com/authApi/sqt/getUser`（`200/0`）、`POST /getUserInfo`（`200/0`）；外呼第三方高德服务 `GET restapi.amap.com/v3/log/init`（`200`）。
- **系统关联**：深度集成高德地图 SDK 与微信/企微二维码分享分发。

### 1.13–1.20 域 1 其余 B 级实用系统（8 站，全部内网独有）
- **`gte.lianjia.com`（北京交易系统 / 签后）**[P0 · B级]：`cas_redirect`，签发 `gte_servers`；展示 `链家网-Link-签后` 入口壳（页面加载未触发额外业务 XHR）。
- **`intcms.jiaoyi.lianjia.com`（交易配置平台）**[P0 · B级]：`cas_redirect`（回调带 `?ticket=[REDACTED]`），签发 `ntcms_session`；纯配置中心壳。
- **`user-ntcs.gte.lianjia.com`（郑州交易可视化系统）**[P0 · B级]：`cas_redirect`，签发 `detect_login_times`、`ke_ntcs_token`、`lianjia_token`；进入 `/list`（`我的交易`）。
- **`cms.newhouse-bd.ke.com`（客发工作台 / 客发 CRM）**[P0 · B级]：`cas_redirect`，签发 `CMS_NEWHOUSE_BD_TOKEN`；顶栏 `退 出`，调用 `GET /api/auth/check`（`200`）。
- **`ishop.lianjia.com`（智慧门店）**[P0 · B级]：`cas_redirect`，签发 `lianjia_token`；渲染门店区域地图 `/admin/area`。
- **`ke-map.ke.com`（壳慧图）**[P0 · B级]：`cas_redirect`，签发 `lianjia_token_employee`；进入 `/admin/home` 地图作业视图。
- **`loushu.lianjia.com`（链楼书）**[P0 · B级]：`cas_redirect`，签发 `lianjia_token`；侧栏提供 `首页`、`精选楼书`、`楼书管理`、`浏览历史`、`个人版`、`收藏`、`草稿`、`团队版`；实测调用 9 个 `/api/ljzc/resblock/album/*`（字典、权限、热门/个人/团队楼书）接口。
- **`mobile.lianjia.com`（LINK-APP 下载页）**[P0 · B级]：`none` 免登录直出（`title="App下载"`，终态 `#/detail/link_secondhand`），分发 Link 二手端移动端包。

> **域 1 C 级边界注记（2 站）**：`saas.a.ke.com`（原 28）已实证为仅含 `GET /cas(302)` 与 `window.__UDL_CONFIG` 的客户端认证空壳；`xfp.ke.com`（上海新房提佣核算）虽加载失败渲染 `title="错误"`，但实测在后端共享域调用成功 `GET i-wp.ke.com/ljzc/xf/performance/server/perm/getPerm` 与 `sysParameter/list`（`200`）。

---

## 域 2：客源、线索、触达与协同通讯域

本域共有 **12 站**实质可用（A 级 8 + B 级 4；`ke.link.lianjia.com` 合流至 `ke.deyoulife.com`，去重后 **11 个独立系统**），**0 站 Grade C**，其中 **5 站直接暴露于公网直连**，是全生态“系统旁路”密集程度最高的对外服务与协同带。

### 2.1 `ke.deyoulife.com`（德佑客源系统）[原28 · A级 · 内网独有]
- **系统定位与归并**：德佑/A+ 核心客源池系统（`title="客源"`，终态 `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer`）；原 28 中旧链家客源/共享池入口 `ke.link.lianjia.com`（A 级）经 CAS 回调后已直接重定向合流至本域名与本页面。
- **认证与 Cookie 族**：`cas_redirect`；同时持有 **A+ 底座**（`MAIN_JSESSIONID`、`_lianjia_link_snid`，合流入口额外含 `saas_token`）与 **德佑/DK 认证簇**（`business_token`）。
- **实测 UI 模块**：启用 **Stencil Shadow DOM** 微前端导航壳承载全客源管理视图（含 `共享池`、`客源待办`）。
- **核心后端 API**：本域 `GET /customer-infrastructure/h5/customer/aiReportEntry`（`200/0`）及 7 个 `/customer-infrastructure/pc/customer/*`（列配置、按钮权限、检索条件、有效客源列表、重复入口、统计汇总）与 `/customer/listPageType`、`/maintain/customer/nav`（均 `200/1`）；跨站调用德佑核心底座 `GET main.deyoulife.com/broker/change/[REDACTED]/` 与 `GET session-saas-a.deyoulife.com/api/getRoleSwitchOptions`。
- **系统关联**：与 `limu.ke.com`、`m-jury.ke.com`、`resume-agc.ke.com` 共用德佑 DK 认证体系；客源端集成 `aiReportEntry` 报表入口。

### 2.2 `ke.link.lianjia.com`（旧客源入口 — 重定向合流至 `ke.deyoulife.com`）[原28 · A级 · 内网独有]
- **系统定位**：枢纽标签为 `共享池`、`客源`、`客源待办`，实测自动跳转并复用 `ke.deyoulife.com` 全套页面与 API，已与后者合并计为同一业务实体。

### 2.3 `clue.a.ke.com`（客户公海 / 我的潜在客户）[原28 · A级 · 内网独有]
- **系统定位**：潜客与公海线索认领平台（`title="线索"`，终态 `https://clue.a.ke.com/page/thread`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `saas_token`。
- **实测 UI 模块**：启用 **Stencil Shadow DOM** 顶栏；侧栏 `全部`、`求购`、`求租`、`出售`、`出租`；筛选卡片 `线索来源`、`线索级别`、`意向区域`。
- **核心后端 API**：`GET clue.a.ke.com/cas`（`302`）、`/api/clue/sea/public/list`、`/api/clue/sea/public/selections`、`/api/pc/header/navigation`、`/api/pc/header/skin`（均 `200/0`）；跨站调用 `GET session-saas-a.ke.com/api/getRoleSwitchOptions`（`200/0`）。
- **系统关联**：与 `shouye`、`ke.deyoulife` 并列消费 `session-saas-a.*` 多域名角色切换中台。

### 2.4 `wandou.ke.com`（豌豆 CRM 普租 / 省心租 IM）[原28 · A级 · 公网直连]
- **系统定位**：面向租住业务的公网实时 IM 与 AI 对话分身工作台（`title="豌豆 PC"`，终态 `https://wandou.ke.com/chat?sessionId=[REDACTED]`）。
- **认证与 Cookie 族**：`localstorage_bearer`（不依赖常规同站业务 Cookie，采用 `localStorage.token` 拼装 `Authorization: Bearer` 头）；依据历史审计 `KE-04` 实证，网关同时接受 `puzu_lease_token` 与 `lianjia_trusteeship_token` 作为有效凭证。
- **实测 UI 模块**：侧栏 `对话`、`笔记`、`知识库`、`AI分身`。
- **核心后端 API**：`GET wandou.ke.com/api/v2/users/me`（`200/0`）、`/api/v2/notes/business-config`、`/api/v2/personal-skills`、`/api/v3/aschat/config`、`POST /api/v3/aschat/inspirations`、`GET /api/v3/aschat/sessions/[REDACTED_CARD]/records`、`/api/v4/aschat/sessions/[REDACTED_CARD]/state`（`200/0`）及事件流 `/api/v4/aschat/sessions/[REDACTED_CARD]/events`（`200`）。
- **系统关联**：公网直连却反向桥接内网普租（`lease-pz`）与省心租托管（`trusteeship`）两大业务线的核心会话与租客对话上下文。

### 2.5 `helper.ke.com`（我的客服 / 业绩争议 / 工单中心）[原28 · A级 · 公网直连 · 别名 `helper.lianjia.com`]
- **系统定位**：集团级员工客服与问题工单总入口（`title="我的客服"`，终态 `https://helper.ke.com/#/`），枢纽涵盖 `业绩争议`、`举报`、`争议处理`、`人事系统问题反馈`、`在线咨询`、`城市工单`。
- **认证与 Cookie 族**：`cas_redirect`；通过统一网关无额外长驻业务 Cookie 直连服务接口。
- **实测 UI 模块**：顶栏 `客服中心`、`首页`、`我的反馈`、`消息 4`、`退出`、`搜索`、`更多`；功能卡 `常用服务`、`常见问题`、`为你推荐`、`联系我们`。
- **核心后端 API**：`POST /web/loginUser/info`（`200/1`）、`GET /web/fe/helpcenter/moduleConfigServiceImpl/getHomePageConfig`（`200/1`）、`/web/fe/knowledge/knowledgeServiceImpl/queryHotTagForC`、`/queryKnowledgeListForC`、`/web/fe/message/innerMailServiceImpl/help/getUnReadCount`、`/web/fe/message/noticeServiceImpl/getOnlineNotice`（均为 `200/1`）。
- **系统关联**：与 `xiaobei.ke.com`、`mknow.ke.com`、`wrule.ke.com`、`heimingdan.lianjia.com` 共同基于统一的 `/web/fe/*` 与 `/web/loginUser/*` Java 知识与工单微服务框架。

### 2.6 `xiaobei.ke.com`（小贝知道 / 财务制度指引）[2A 新增 · A级 · 公网直连]
- **系统定位**：公网可达的面向业务线智能问答与规则指引库（`title="帮助"`，终态 `https://xiaobei.ke.com/#/`）。
- **认证与 Cookie 族**：`cas_redirect`；以统一 `/web/loginUser/checkUserLogin` 及 `/info` 校验会话。
- **实测 UI 模块**：高频知识问答卡片（涵盖 `IM拉新显示`、`商机转客源`、`M作战小组任务分`、`跨城业绩分配`、`推公规则`、`看房报告`、`行程量带看/陪看口径` 等业务细节）。
- **核心后端 API**：实测 10 个接口，核心包括 `/web/loginUser/checkUserLogin`、`/web/loginUser/info`、`/web/fe/helpdesk/saasSkinServiceImpl/changeSkin`、`/web/fe/knowledge/agentConfigApiServiceImpl/getDetailsWithSub`、`/configurationServiceImpl/getConfigurationDTOListForC`、`/knowledgeOpenApiServiceImpl/search`、`/knowledgeServiceImpl/dataInitForEndpoint`、`/pageConfigServiceImpl/getPageNameForType`、`/relationalServiceImpl/getNewFistRelationalTreeListForC`。
- **系统关联**：虽为独立公网入口，但被内网主站 `shouye.link.lianjia.com` 与 `link.lianjia.com` 在首页加载时主动跨站拉取知识标签与登录态检查，是典型的“公网接口反哺内网工作台”共享知识服务。

### 2.7 `luoshu.ke.com`（洛书动态表单平台）[2A 新增 · A级 · 公网直连]
- **系统定位**：企业通用低代码动态表单引擎（`title="洛书"`，终态 `https://luoshu.ke.com/dynamicFormRender?definitionCode=[REDACTED]&mode=run&params=%7B%7D`），枢纽入口复用承担 `[新]售前信息采集`、`印章管理`、`干部管理系统`、`访客预约`、`银河计划`、`首看保护` 等 6 类核心流程提交。
- **认证与 Cookie 族**：`cas_redirect`；签发 `lianjia_token_sinan`、`lianjia_token_sinan_prod`（司南体系 Cookie）。
- **核心后端 API**：`GET /web/login/cas`（`302`）、`GET /web/loginUser/info`（`200/1`）、`POST /web/fe/v1/form-plat/dynamicFormWebServiceImpl/initDynamicForm`（`200/1`）。
- **系统关联**：沿用统一 `/web/fe/*` 与 `/web/loginUser/*` 架构，单域名按 `definitionCode` 挂载六大行政、人事与作业申报表单。

### 2.8 `m-jury.ke.com`（B 端纠纷调解系统）[2A 新增 · A级 · 公网直连]
- **系统定位**：经纪人与服务者之间作业/业绩纠纷线上调解工作台（终态 `https://m-jury.ke.com/new/mediation/agentList`）。
- **认证与 Cookie 族**：`cas_redirect`；同时接入 **德佑/DK 簇**（`ke_dk_u_id`、`ke_dk_u_name`、`ke_do_dk_u_id`）与 **新房工具簇**（`lianjia_token`）。
- **实测 UI 模块**：侧栏状态切换 `全部`、`未完结`、`已完结`。
- **核心后端 API**：`GET /api/jury/getUserInfo`、`GET /api/mediation/agentMediationList`、`GET /api/mediation/mediationOpenCity`（全 `200/0`）。
- **系统关联**：横跨德佑认证簇与通用 `lianjia_token`，将纠纷处理直接暴露在公网直连边界以便利一线作业方随时跟进。

### 2.9–2.12 域 2 其余 B 级系统（4 站，全部内网独有）
- **`xiaoxi.link.lianjia.com`（消息与动态系统）**[原28 · B级]：`cas_redirect`，签发 `MESSAGEJSESSION`；保留全套老链家八项顶栏（`首页`、`房源`、`客源`、`签约`、`签后`、`应用`、`图灵`、`消息`）及 `系统消息` 视图。
- **`kemcc.ke.com`（通知中心 / 触达平台）**[原28 · B级]：`cas_redirect`，签发 `roleId`、`survey_target_token`；默认 `/templatesManage` 展示 `push` 顶栏并调用 `/api/permission/getPermsByRoleId`、`/api/permisson/getPermByRole`、`/getRoleByUcid`；历史实测 `/noticeCenter` 路径联动即时通讯网关 `imapi.lianjia.com`。
- **`notice.ke.com`（任务/通知/新个人信息模块）**[P0 · B级]：`cas_redirect`，签发 **工作台任务簇** `task-databoard-cookie`（与 `shouye.link.lianjia.com` 共享）；汇聚 `任务模块`、`新个人信息模块`、`榜单模块`、`通知模块`。
- **`haoma.lianjia.com`（号码通 — 免打扰电话查询）**[P0 · B级]：`cas_redirect`，签发 `helper_lianjia_com`（与客服系同源命名），展示 `号码通` 查号入口卡。

---

## 域 3：商业化展位、规则风控与运营工具域

本域共有 **11 站**实质可用（A 级 7 + B 级 4，**全部内网独有**），另有 **1 站 Grade C**（`hx.ke.com`）。核心聚焦贝壳币账户、金贝/留链/商圈展位报买投放、作业物料生产（火石/立木/大屏/便民）以及全网规则与黑名单风控。

### 3.1 `b.ke.com`（币平台）[P0 · A级 · 内网独有]
- **系统定位**：内部“贝壳币”商业化结算与账户资产管理平台（`title="币平台"`，终态 `http://b.ke.com/`）。
- **认证与 Cookie 族**：`cas_redirect`；CAS 会话承接 `SESSION`/`lianjia_ssid`。
- **实测 UI 模块**：顶栏 `退出`；侧栏 `我的账户`、`预算管理`、`查看详情`；展示 `贝壳币` 账户列表。
- **核心后端 API**：`GET /web/account/currencies`、`GET /web/user/accountAssetCard`、`POST /web/user/searchAccountList`、`GET /web/user/searchCondition`（全 `200/0`）。
- **系统关联**：支撑商业化展位报买与平台币充值消耗记账。

### 3.2 `brand.ke.com`（金贝展位推广平台）[原28 · A级 · 内网独有]
- **系统定位**：房源商业化广告展位（金贝）预算与报买竞价平台（`title="金贝平台"`，终态 `https://brand.ke.com/#/index/home`）。
- **认证与 Cookie 族**：`cas_redirect`。
- **实测 UI 模块**：顶栏 `金贝展位推广平台`、`首页`、`展位管理`、`展位报买`、`个人中心`、`退出`。
- **核心后端 API**：实测 **9 个本域接口**（全 `200/1`），含 `/api/budget/list`、`/showData`、`/api/perbid/advice/show`、`/allOpyIndex`、`/banner`、`/getBusinessLine`、`/api/user/queryUserBlackStatus`、`/role/current`、`/role/list`。
- **系统关联**：与 `liulian.ke.com`、`newhouse-cps.ke.com` 共同构成二手与新房展位商业化变现矩阵。

### 3.3 `liulian.ke.com`（展位管理后台 / 留链展位报买）[原28 · A级 · 内网独有]
- **系统定位**：商机与评论展位报买管理系统（`title="留链-展位报买"`，终态 `http://liulian.ke.com/static/buy/commentExpo`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `SESSIONID`。
- **实测 UI 模块**：顶栏 `注销`、`买卖`。
- **核心后端 API**：`GET /cas`（`302`）、`GET /dmp/queryDisplayInfo`（`200/1`）、`GET /dmp/userPos`（`200/1`）。
- **系统关联**：底层对接 DMP 展位投放引擎（`/dmp/*`）。

### 3.4 `wrule.ke.com`（贝壳规则主站）[P0 · A级 · 内网独有]
- **系统定位**：ACN 规则动态、平台监察公示与规则解读门户（`title="贝壳规则"`，终态 `https://wrule.ke.com/#/`）。
- **认证与 Cookie 族**：`cas_redirect`。
- **实测 UI 模块**：核心卡片展示 `规则动态`、`平台监察`、`规则解读`、`平台-什么是用户投诉？`。
- **核心后端 API**：实测 **12 次请求**覆盖 `/web/fe/rule/ruleCommonServiceImpl/checkAcnAuthority`、`/getBannerInfo`、`/getUserInfo`、`/rulePcServiceImpl/getPcSkin`、`/homePageList`、`/supervisionInfo`（登录后均返回 `200/1`）。
- **系统关联**：与 `heimingdan.lianjia.com`、`helper.ke.com`、`xiaobei.ke.com` 同属统一 `/web/fe/*` 知识与规则中台集群。

### 3.5 `heimingdan.lianjia.com`（黑名单 / 规则罚则公示）[P0 · A级 · 内网独有]
- **系统定位**：同业违规黑名单与 ACN 奖惩规则公示页（`title="贝壳规则"`，终态 `http://heimingdan.lianjia.com/#/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `projectCode`。
- **实测 UI 模块**：展示 `规则/罚则公示`、`ACN规则`、`奖惩规则`、`最新宣传`。
- **核心后端 API**：本域调用 `/web/fe/an/rule/ruleCommonServiceImpl/acnRuleList`、`/acnRuleRagOpen`、`/isUpgrade`、`/punishRuleList`、`/preventiveEduServiceImpl/getAccumulatedViolationCount`、`/ruleCommonServiceImpl/checkAcnAuthority`、`/entryList`、`/getBannerInfo`、`/homePageList` 及 `/web/loginUser/info`（全 `200/1`）；**异常架构信号**：页面同时向云内网测试域发起 `GET rule-chat.ttb.test.ke.com/api/chat/fixed_script`（`302`）。
- **系统关联**：规则展示与 `wrule.ke.com` 同源，但生产页面仍遗留指向 RFC 6598 CGNAT 测试环境（`*.ttb.test.ke.com`）的智能规则问答话术拉取请求。

### 3.6 `limu.ke.com`（立木营销海报设计平台）[P0 · A级 · 内网独有]
- **系统定位**：面向一线经纪人的营销海报与设计模板协作平台（`title="为你推荐"`，终态 `https://limu.ke.com/recommend`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 **德佑/DK 认证簇**（`ke_dk_u_id`、`ke_dk_u_name`、`ke_do_dk_u_id`）及专属 `limu_ke_token`、`homepageGuide`。
- **实测 UI 模块**：顶栏 `跳过`；侧栏 `为你推荐`、`我的工作台`、`创建设计`、`模板中心`、`我的团队`、`智能工具`；卡片载明 `新人指引` 及四项使用/违规管理规则。
- **核心后端 API**：实测 **7 个 `200/0` 接口**：`/api/config/getApollo`、`/api/my/info`、`/api/public/recommend/detail`、`/recommend/list`、`/template/list2`、`/wxwork/qrcode`、`/api/team/list2`。
- **系统关联**：注意 `lingjing.ke.com`（立木/灵境另一候选）被 HIP 拦截（E 级），而 `limu.ke.com` 在内网完整可用，并与 `ke.deyoulife`、`m-jury`、`resume-agc` 共享 DK 身份簇。

### 3.7 `content.lianjia.com`（国民链家 / 便民服务后台）[P0 · A级 · 内网独有]
- **系统定位**：门店打印与社区便民服务订单管理后台（`title="国民链家后台"`，终态 `https://content.lianjia.com/service/order/print`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `ljzc_token`。
- **核心后端 API**：`GET content.lianjia.com/info`（`200`，完成主体核验）。

### 3.8–3.11 域 3 其余 B 级系统（4 站，全部内网独有）
- **`newhouse-cps.ke.com`（商圈房源聚焦 / 配置管理系统）**[原28 · B级]：`cas_redirect`，签发 `Lianjia_BUcid`、`Lianjia_curWorkCity`、`PC_CPS_TOKEN`；顶栏 `退出`；侧栏囊括 `展位配置平台`、`自然展位管理`、`商业展位管理`、`POC管理`、`数据管理`、`新房CPT`、`服务者认证管理`、`商圈聚焦`（含聚焦场次/现场/复盘）；调用 `/api/wolverine/houseFocus/*`（`ShopUpload/isDownloadNew`、`queryActivityList`、`queryUserRule`）。
- **`rulemanage.ke.com`（蓝鲸 — 同业经纪人黑名单查询）**[P0 · B级]：`cas_redirect`，签发 `SESSIONID`、`lianjia_token`；进入 `#/widget/common/black/list`，调用 `/web/cas`（`303`）及 `/web/loginUser/checkUserLogin`、`/info`、`/menus`。
- **`flint.lianjia.com`（火石作业物料平台）**[P0 · B级]：`cas_redirect`，签发 `flint`；顶栏 `返回link首页`、`贝壳火石`、`作业物料`、`关于火石`；主推五大报告卡片 `二手带看报告`、`换房方案 二手房`、`新房置业报告`、`房源信息纸`、`楼盘信息纸`；调用 `/api/flint/question` 与 `/flint/permission`。
- **`cms-tv.ke.com`（门店培训大屏内容管理系统）**[P0 · B级]：`cas_redirect`，签发 `cms-tv_ke_com`；管理大屏二手房源与新房视频轮播（`/carousel/new`）。

> **域 3 C 级边界注记（1 站）**：`hx.ke.com`（红星运营平台）经 CAS 登录后 `/role/list` 返回 `200/100000` 无操作角色并自动跳转至 `/notauth`。

---

## 域 4：AI 原生平台、数据智能与分析域

本域共有 **14 站**实质可用（A 级 3 + B 级 11；其中 **5 站公网直连可用**，**9 站内网独有可用**），另有 **1 站 Grade C**（`app-moma.ke.com`）。核心呈现三大支柱：**Moma 多租户轻应用与火山引擎 Supabase 体系**、**Odin 大数据与对话式 BI 簇**、以及 **`is-prod` 嵌入式智能技能助手与垂直 AI 引擎**。

### 4.1 `moma.ke.com`（Moma 轻应用与 AI 原生工作台）[原28 · A级 · 公网直连]
- **系统定位**：企业级多租户 AI 助手与轻应用汇聚主平台（`title="MOMA-首页"`，终态 `https://moma.ke.com/nchat`）。
- **认证与 Cookie 族**：`supabase_cas`（CAS 回跳换取长期业务 JWT Cookie 并衔接 Supabase/GoTrue）；签发 **13 个 Moma 簇 Cookie**（`prd-assistant-token-prod`、`X_Access_Token`、`MOMA_LANGUAGE`、`X-Product-Type`、`_ucid_`、`_account_`、`_usercode_`、`_displayName_`、`_userFullEmail_`、`smart_mo_admin_session`、`smart_mo_xiaomo_logged_out`、`smart_mo_xiaomo_login_intent`、`timezone`）。**历史安全实证（`KE-24`）**：`prd-assistant-token-prod` 有效期至 `2083`，CAS `/logout` 吊销 TGC 后该令牌仍可独立访问 `prd-assistant-backend.ke.com`。
- **实测 UI 模块**：顶栏 `使用`；左侧 **9 项 AI 能力导航**（`新建 New`、`发现 Discover`、`应用 Apps`、`专家 Experts`、`技能 Skills`、`空间 Space`、`数据 Data`、`连接 Connectors`、`计划 Plan`）；首页精选应用团队卡包含 `新房千智团队`、`智慧运营助手·智大星`、`KE-AOE 团队`、`大数据团队`、`贝壳找房专家团队`、`豌豆团队` 及 `下载MOMA App`。
- **核心后端 API（实测 40 个非登录端点）**：
  - 本域：`GET /skill-hub/api/v1/session/app-link-list`、`/user-skill/all-installed`（`200/0`）；
  - 核心后端 `prd-assistant-backend.ke.com`（30 次请求）：`/api/admin/check-access`、`/api/chats`（GET/POST）、`/api/chats/{chat_id}/get_config`、`/api/chats/lite_app/mention/user_resource`、`/api/feature/memory-management`、`/voice-input`、`/api/gateway/agi-agent/v1/friday-ai/me`、`/user/config/agent-config`、`/model-lab`、`/v2/agent-flash/access`、`/api/homepage-cards`、`/api/moma-data/data-assets/accessible`、`/api/partner-community/summary`、`/partner-showcase/cards`、`/api/public/homepage/bootstrap`、`/api/recent-artifacts`、`/api/tab-display/access`、`/api/user/ai-analysis-access`、`/assistant-access`、`/check-auth`、`/clear-cookies`、`/config`、`/default-skills/sync`、`/homepage/personal-bootstrap`、`/info`、`/api/v1/switchable-models`（全部返回 `200`）；
  - 关联扩展后端 `smart-mo-api.ke.com`（7 次请求）与 `smart-mo.ke.com`（1 次）：`/api/auth/callback`（`200`）、`/api/auth/me`（`401`）、`/api/browser-extensions/current`（`200`）、`/api/xiaomo/auth/login`（`302`）、`POST /api/xiaomo/pet/bootstrap`（`200`）、`/api/xiaomo/bootstrap` 与 `/page-visits`（`401`）。
- **系统关联**：公网直连却汇聚全集团新房/大数据/运营/豌豆 AI 轻应用、`moma-data` 资产目录、`friday-ai` 与小莫浏览器扩展网关。

### 4.2 `odin.ke.com`（Odin 奥丁数据分析平台）[原28 · A级 · 内网独有]
- **系统定位**：企业核心 BI 分析、数据探查与管理驾驶舱平台（`title="奥丁数据分析平台"`，终态 `https://odin.ke.com/`），枢纽入口涵括 `奥丁`、`管理驾驶舱`、`上海数据中心`、`上海星级维护`。
- **认证与 Cookie 族**：`cas_redirect`；签发 **Odin 大数据簇** `odin_token`、`dwh-datascreen_token`。
- **实测 UI 模块**：顶栏 **6 大模块** `首页`、`工作台`、`数据查询`、`数据魔方`、`数据探查`、`社区`。
- **核心后端 API**：`GET /ajax/user/me`、`GET /kegate/odin/agent/hasGray`、`/kegate/proxy/odin/ubag/common/api/odin_space_list`、`/odin_white_list`、`POST /odin_indexpage_message_notify`、`/odin_preference_theme_set`（均 `200/1`）。
- **系统关联**：通过统一网关路径 `/kegate/*` 与同族 `codin.ke.com`、`chatbi.ke.com` 共享空间权限与数据集目录。

### 4.3 `codin.ke.com`（CHO 驾驶舱 — Odin 大数据家族）[P1 · A级 · 内网独有]
- **系统定位**：面向管理层与人力高管（CHO）的奥丁专题驾驶舱入口（`title="奥丁数据分析平台"`，终态 `https://codin.ke.com/portal/3956/49460`）。
- **认证与 Cookie 族**：`cas_redirect`；共享 `odin_token`、`dwh-datascreen_token`。
- **核心后端 API**：`GET /ajax/user/me`、`/kegate/odin/agent/hasGray`、`/kegate/proxy/secret/ubag/common/api/odin_space_list`、`/odin_white_list`、`POST /odin_preference_theme_set`（全 `200/1`）。
- **系统关联**：实质为 `odin.ke.com` `/portal/{space}/{page}` 门户实例的独立二级域名映射，底层复用全套 `/ajax/user/me` 与 `/kegate/*` 代理。

### 4.4 `chatbi.ke.com`（Chat BI 智能问数）[2A 新增 · B级 · 公网直连]
- **系统定位**：公网可达的自然语言对话式商业智能分析系统（`title="Chat BI"`，终态 `https://chatbi.ke.com/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 **Odin 簇** `odin_token` 与专属 `bigdata_bicopilot_token`。
- **实测 UI 模块**：启用 **Stencil Shadow DOM**；侧栏 `历史`、`技能`、`知识`。
- **核心后端 API**：`GET /ajax/bicopilot/getUcLabel`（`200/200`）、`POST /ajax/chatbi/getBizType`、`GET /ajax/chatbi/getChatModelsList`、`POST /getSessionPageList`、`POST /getUserLabel`、`GET /ajax/common/getPermissionMenu`（均为 `200/0`）。
- **系统关联**：将内网 `odin.ke.com` 的数据指标与 BI 问数能力通过独立 `bigdata_bicopilot_token` + `odin_token` 暴露于公网边界。

### 4.5 `letsai.ke.com`（Let's AI 企业 AI 门户）[原28 · B级 · 公网直连]
- **系统定位**：公司内部 AI 工具集市、实践课程与案例布道门户（`title="Let's AI | AI门户"`，基于 WordPress + CAS 构建）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `PHPSESSID`、`wordpress_logged_in_*`、`wordpress_sec_*`、`wordpress_test_cookie`。
- **实测 UI 模块**：顶栏 `AI布道师主页`、`贝壳AI智能体合集，效率翻倍！`；侧栏分类包含 `内部应用专区`、`AI模型应用`、`AI办公工具`、`AI图像工具`、`AI公开课`、`Vibe Working实践案例`、`人财法工具专区`、`AI工具教程`、`AI实践分享`、`我要投稿`。
- **核心后端 API**：静态渲染页面直出，未触发独立 JSON XHR；站内链接指向全网可用及被拦截的多类 AI 内部与外部工具。

### 4.6 `is-prod.ke.com`（闹海 / 小伯乐智能技能中心）[2A 新增 · B级 · 公网直连]
- **系统定位**：平台级嵌入式智能助手“小伯乐 / 闹海”主控端与跨站技能分发引擎（`title="闹海"`，终态 `https://is-prod.ke.com/pages/naohai`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 **智能服务簇** `intelligentService_sid`、`intelligentservice_token`。
- **核心后端 API**：`GET is-prod.ke.com/admin/skill/queryMainPage`（`200/0`）。
- **系统关联**：全生态极具代表性的 **公网暴露 + 内网横向嵌入** 共享能力枢纽——同时被内网 **7 个站点**（`hr`、`hrlearn`、`recruit`、`contract`、`cb`、`lcm`、`leader`）写入同簇 Cookie 并直接跨站调用 `/admin/skill/queryMainPage`（7 站内另有 5 站触发真实跨域 API 请求，参见拓扑表）。

### 4.7 `aiglasses.ke.com`（智眸 AI 眼镜平台）[2A 新增 · B级 · 公网直连]
- **系统定位**：智能硬件“智眸 AI 眼镜”产品门户与文件视频协同平台（`title="智眸 - 镜在眼前，智慧搭伴"`）。
- **认证与 Cookie 族**：`none`（免登录匿名直出）。
- **实测 UI 模块**：顶栏 `首页`、`视频列表`、`文件上传`、`时刻创新与突破`；能力矩阵卡片展示 `图像语音混合采集`、`高清1:1还原场景挖掘`、`信息宝典，AI协作`、`提词神器，聚焦演讲`、`解锁健康，游艺空间`、`全域定制，随需生长`。

### 4.8–4.14 域 4 其余内网独有 B 级系统（7 站）
- **`biz-data.ke.com`（业务事件平台）**[P0 · B级]：`cas_redirect`，签发 `datacenter_token`；顶栏 `数据开放平台`、`业务事件平台`；侧栏 `业务概览`、`查询申请`、`订阅管理`、`事件重放`、`质量监控`、`发布方管理`、`接入管理`；卡片贯通 `房源`、`客源`、`签中`、`新房`、`客源线索`、`商业化` 六大事件域；调用 `/api/common/getMenuAuth` 与 `/api/overView/overView`（`200/100000`）。
- **`fast.ke.com`（天眼系统 FAST）**[P0 · B级]：`cas_redirect`，跳转经 `secondx.data.lianjia.com` 后签发 `_secondx`；调用 9 次接口含 `POST /app/issuperadmin`、`POST /app/user/searchAppByUser`、`GET /dict`、`/statis/getDefaultAppCode`、`/statis/resource/user`、`POST /user`。
- **`compass.ke.com`（罗盘 / 埋点平台）**[P2 · B级]：`cas_redirect`，签发 `compass_token`；跨站调用 `GET compass-server.ke.com/login/cas`（`302`）及本域 `/loginUser/info`（`200`）。
- **`data.ke.com`（新房业务、每日关注看板）**[P0 · B级]：`cas_redirect`，签发 `board_data_unify`；指标经营看板主域（无额外捕获 XHR）。
- **`speechhub.ke.com`（贝壳语音算法平台）**[2A→2B-1 · B级]：公网建连关闭、内网免登录直出；启用 **Shadow DOM**；顶栏 `SpeechHub`、`首页`、`Skills & CLI`、`算法能力`、`能力体验`、`接口文档`、`业务场景`、`语音评测`；卡片提供 `ASR`、`TTS`、`VPR`、`VAD`、`SLM` 六大语音模型体验与 CLI 下载，调用 `/audiobench`、`/cli`、`/docs`、`/playground`、`/research`、`/solutions`、`/support`。
- **`ketest-pro.ke.com`（Ketest 一站式质量平台 / Copilot）**[2A→2B-1 · B级]：公网建连失败、内网 `cas_redirect` 可用；签发 `KETEST-SESSION`；调用后端网关 `ketest.api.ke.com/authagent-api/auth2/`（`302`）与 `/authagent-api/check/`（`200`）。
- **`chat-excel.ke.com`（CHAT-EXCEL）**[2A→2B-1 · B级]：公网不可达、内网直出可用（`title="CHAT-EXCEL"`）。

> **域 4 C 级边界注记（1 站）**：`app-moma.ke.com`（Moma 多租户轻应用托管域，公网 `not_counted` 复测）不带具体 `/app/{uuid}` 访问根路径时经 Supabase CAS 签发全套 10 个 Moma Cookie 后跳转 `moma.ke.com/deploy_404`（7 个 `prd-assistant-backend` 个人接口在未挂载应用上下文时返回 `401` 或公开页 `200`）。依据历史审计 `kecom-security-audit-report-2026-11-20.md`，所有轻应用部署在同一源下共享 `localStorage` 与 `/falcon/supabase-volc/*` 网关。

---

## 域 5：员工门户、HR 人事、考勤薪酬与培训绩效域

本域共有 **16 站**实质可用（A 级 4 + B 级 12，**全部内网独有**），另有 **1 站 Grade C**（`shebao.ke.com`）。核心围绕 **`family` / `hr` 全员工作门户**、**`nltx` 职级**、**招聘与人才库**、**薪酬中台 4 站集群**及 **贝好学/花桥学习体系**展开。

### 5.1 `family.ke.com`（员工门户 / Family）[原28 · A级 · 内网独有 · 别名 `home.ehr.lianjia.com`]
- **系统定位**：全体正式员工行政、人事、办公服务一级总门户（`title="贝壳"`，终态 `https://family.ke.com/home/portal`），枢纽标签覆盖 `Bella`、`传阅我的`、`体系外经历`、`内网导航`、`利益关系`、`制度查看`。
- **认证与 Cookie 族**：`cas_redirect`；签发 `family_front_token`、`family_prod`、`x-tenant-id`。
- **实测 UI 模块**：顶栏包含全员统一样式的 **8 项集团一级导航**：`首页`、`新文化内核`、`员工门户`、`AI门户`、`消息`、`邮箱`、`我要反馈`、`贝壳`。
- **核心后端 API**：`GET /api/user/profile`（`200/100000`，完成员工主体核验）与 `GET /api/nickname/status`（`200/100001`）；历史 Phase 1 还观测到 WebPush 网关 `msg.ke.com/sound/wave/webpush/server/v1/notice`。
- **系统关联**：与 `hr.ke.com`、`jixiao.ke.com`、`kaoqin.hr.ke.com`、`recruit.ke.com`、`contract.ke.com` 共享完全统一的员工平台顶部导航栏。

### 5.2 `nltx.ke.com`（积分职级平台）[P2 · A级 · 内网独有]
- **系统定位**：服务者与经纪人的实时积分明细及月度职级评定查询系统（`title="积分职级"`，终态 `https://nltx.ke.com/pageo/integral-detail`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `agent_score_system_sid`。
- **实测 UI 模块**：顶栏 `积分详情`、`退出`；展示 `积分概览`、`当前职级（每月更新）`、`当前积分（每日更新）`、`本月初职级`、`预计下月初职级`、`积分明细`、`本月带队积分明细`、`本月贡献积分明细`。
- **核心后端 API**：实测 **7 个 `200/0` 接口**：`/common/getConstant`、`/session/user`、`/session/user/permissions`、`/user/comp_info`、`/user/contribute/score/detail`、`/user/scoreOfLead/detail`、`/user/user_info`。
- **系统关联**：与 `8.lianjia.com`（D 级无权）共用同代 `/pageo/*` 与 `/session/user` 积分架构，但 `nltx.ke.com` 对普通员工账号完全开放。

### 5.3 `study.ke.com`（贝壳学堂 / 贝好学在线课堂）[原28 · A级 · 内网独有 · 别名 `elearning.ke.com`]
- **系统定位**：企业全员在线学习、AI 实战训练营与岗位认证课程平台（`title="在线课堂"`，终态 `https://study.ke.com/courseList`）。
- **认证与 Cookie 族**：`cas_redirect`；与 `admin-study.ke.com`（D 级管理端）共享 `renovation-token`。
- **实测 UI 模块**：首页展示大量 AI 工具实践课（`Trae`、`Vibe Working`）及业务交付、服务赔付认证课列表。
- **核心后端 API**：实测 **9 个接口**（多为 `200/0`），含 `/api/gbc/renovation/platform/general/v1/roles`、`/mobile/operation/v1/review/my/count`、`/p/online-course/v1/course/typeListByOrg`、`/org/list-tree`、`/v2/course/page`、`/p/page-config/v1/get-home-page`、`/teach-manage/v1/mentor/is-mentor`、`/user/user/v1/info` 及 `/web/login/user/info`。

### 5.4 `m-intelligencecampus-hq.ke.com`（老左图书馆）[P2 · A级 · 内网独有]
- **系统定位**：花桥国际校园 / 图书馆线上规则与预约系统（`title="老左图书馆"`，终态 `https://m-intelligencecampus-hq.ke.com/LibraryBooking/bookingIndex`）。
- **认证与 Cookie 族**：`cas_redirect`；与 `admin-huaqiao.ke.com` 共享 **花桥学堂簇** `huaqiao_token`。
- **核心后端 API**：`GET /api/v1/user/current`（`200`，完成主体核验）与 `GET /api/v1/library/rule/list`（`200`）。

### 5.5–5.16 域 5 其余内网独有 B 级系统（12 站）
- **`hr.ke.com`（KeHR — HR 服务总门户）**[原28 · B级]：`cas_redirect`，同时签发 `ke` 与 **智能服务簇** `intelligentService_sid`/`intelligentservice_token`；侧栏集成 `KeHR`、`配置管理`、`实时员工花名册`、`LinkHR`、`合同管理`、`考勤系统`、`内档系统`、`调岗工作台`、`离职工作台`、`限制资格查询`；调用 `/graphql`（`200`）与 `/portal/systems/all`（`200/1`），为全集团核心 HR 系统聚合导航枢纽。
- **`kaoqin.hr.ke.com`（考勤 SAAS 系统）**[原28 · B级]：`cas_redirect`，签发 `lianjia_kaoqin_login_token`；侧栏涵盖 `考勤打卡`、`考勤记录`、`余额`、`日常考勤`、`我的考勤日历`、`考勤审批`、`考勤总览`、`余额明细`、`排班管理`；调用 7 个 `/attendance/path/*`、`/balance/path/statistic`、`/employee/getEmployeeByUserCode`、`/link/menu`、`/permission/roleAndPerm`、`/redirect/getRedirectFlag`（均 `200/100000`）。
- **`xinchou.ke.com`（薪酬核算系统 — 工资单与银行卡入口）**[原28 · B级]：`cas_redirect`，签发 `roleId`、`xinchou_token`；顶栏 `首页`、`人事`、`薪酬`、`考勤`、`绩效`、`员工关怀`、`退出`；侧栏含 `岁岁有余`、`工资卡号`、`点我修改`、`语音验证码`；调用 `/sapi/sm/ps/v2/salary/landing`、`/sapi/v1/salary/menu`、`/user/verify`（`200`）。
- **`jixiao.ke.com`（职能绩效与试用期系统）**[2A→2B-1 · B级]：公网异常转入内网可用；`cas_redirect`，签发 `jixiao_ke_com`、`jixiao_ke_com.sig`；涵盖 `制定试用期目标`、`查看转正结果`、`绩效目标/自评/结果`，调用 `GET /user/logined`（`200/100001`）。
- **`recruit.ke.com`（贝壳招聘 / Offer 与入职管理）**[P1 · B级]：`cas_redirect`，签发 `lianjia_token_zhaopin` 并嵌入 **智能服务簇**（`intelligentService_sid`/`intelligentservice_token`）跨站调用 `GET is-prod.ke.com/admin/skill/queryMainPage`；本域调用 `/papi/i/talent/offer/manage/enums/all`、`/v1/enumeration/fetch` 及 `/papi/zhaopin/*` 共 7 个接口。
- **`profile.ke.com`（贝壳人才管理系统）**[P1 · B级]：`cas_redirect`，签发 `profile_pc_token`；终态 `/talent`，调用 `/api/talent/auth/getUserRoleList`（`200/0`）。
- **`resume-agc.ke.com`（聘多多内网入口）**[P2 · B级]：`cas_redirect`，签发 `mag_ke_com` 与 **德佑/DK 认证簇**（`ke_dk_u_id`、`ke_dk_u_name`、`ke_do_dk_u_id`）；区别于被 HIP 拦截的 `jianli.ke.com`，内网进入 `/talent/candidateList` 并调用 `/api/newOauth/agc/interview/api/b/agc/interview/pdd/check-in-grey` 与 `/api/oauth/talent/axe/server/v2/axe/pdd/employee/find-ucId-v2`。
- **`salary-middle.ke.com`（薪酬中心）**、**`salary-tax.ke.com`（税友中心）**、**`salary-monitor.ke.com`（服务监控）**、**`lcm.ke.com`（预实管理系统）**[均 P2 · B级]：构成紧密耦合的 **薪酬中台 4 站工作集群**，前二者共享 `salary_middle_token`，三者共用五模块顶栏（`薪酬中心`、`税友中心`、`核算平台`、`预处理平台`、`服务监控`）；`salary-monitor` 签发 `salary_monitor_token` 并调用 `/api/v1/auth/role/info`、`/roles`、`/dict/all`；`lcm.ke.com` 签发 `salary_budget_actual_sid` 与 **智能服务簇** Cookie 并跨站调用 `is-prod.ke.com/admin/skill/queryMainPage`。
- **`admin-huaqiao.ke.com`（花桥学堂 CMS）**[P2 · B级]：`cas_redirect`，共享 `huaqiao_token`；调用 `/api/v1/common/base`、`/api/v1/user/perm`、`/api/v1/user/roles`（另两个奖学金/任务字典接口返 `417/-1`）。

> **域 5 C 级边界注记（1 站）**：`shebao.ke.com`（社保公积金管理系统）签发 `ssc-token` 后 `POST /currentUserApi/taskCount` 返回 `200`，但 `POST /currentUserApi/currUser` 返回 `401` 导致业务菜单未渲染。

---

## 域 6：财务费控、资产、法务合规与审批中台域

本域在 P1/P2 新发现条目最多（深扫 24 站），**8 站实质可用**（A 级 2 + B 级 6，**全部内网独有**），同时汇集了全生态一半以上的 **C 级部分受限/空壳站点（8 站）**，真实映射出企业财务/费控/BPM 新旧中台并行的复杂过渡状态。

### 6.1 `budget-control.ke.com`（预算管控系统）[P1 · A级 · 内网独有]
- **系统定位**：企业部门费用预算编报、执行与流程管控核心系统（`title="预算管控系统"`，终态 `http://budget-control.ke.com/#/`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `budget_control`。
- **实测 UI 模块**：顶栏 `我要反馈`、`退出`；侧栏 `文件下载`、`日志管理`。
- **核心后端 API**：`GET /api/auth/getLoginUser`、`/api/auth/getUserPermission`（`200/1`，核验主体一致）、`/budget/config/base`（`200`）、`/process/budgetAssistant/access`（`200/200`）、`POST /process/config/getOrderData`、`/getWorkFlowTypeData`（`200/1`）。

### 6.2 `mknow.ke.com`（违纪通报 / 知识库）[P1 · A级 · 内网独有]
- **系统定位**：面向全员公示企业合规违纪通报与规章目录的企业知识门户节点（`title="知识列表"`，终态 `https://mknow.ke.com/#/list/28981?...`）。
- **认证与 Cookie 族**：`cas_redirect`。
- **核心后端 API**：`GET /web/loginUser/info`（`200/1`）、`/web/fe/knowledge/knowConfigServiceImpl/getSkin`、`/knowledgeOpenApiServiceImpl/search`、`/relationTreeOpenApiServiceImpl/getCatalogList`（均 `200/1`）。
- **系统关联**：与 `helper.ke.com`、`xiaobei.ke.com`、`wrule.ke.com` 共用同一套知识树检索底座。

### 6.3–6.8 域 6 其余内网独有 B 级系统（6 站）
- **`bfa.ke.com`（财务+ 一站式财务门户）**[P1 · B级]：`cas_redirect`，签发 `koin_token`；顶栏汇集 **7 大财务子域**：`对账平台`、`资金系统`、`记账平台`、`商业化`、`财务中台`、`线下收款认款`、`结算平台`；侧栏 `常用`、`政策`、`制度`、`公告`；调用 `/api/koin/v1/config/base` 与 `/permission`。
- **`pay.ke.com`（Kepay 贝壳 Pay）**[P1 · B级]：`cas_redirect`，签发 **3 个报销与支付令牌**（`pay_ke_token`、`pay_ke_token_up_.ke.com`、`tiper_token_up_.ke.com`）；侧栏全面开放 `新建报销`、`单据列表`、`EBS-AP`、`发票列表`、`待处理`、`已处理`、`我的`；调用 `POST /papi/fssc/server/server/reimburse/v2/getUnhandleList`（`200/1`）。
- **`kepler.ke.com`（税务平台）**[P1 · B级]：`cas_redirect`，签发 `kepler_token`（终态 `/invoice-apply`）；顶栏 `销项发票管理`、`进项发票管理`、`税务主数据管理`；侧栏 `发票申请`、`流程中心`、`资质申请`、`手工开票管理`、`数电登录`；调用 `/api/v1/apply/pagination` 与 `/api/v2/dict`。
- **`mzichan.ke.com`（我的资产 / 资产申请）**[P1 · B级]：`cas_redirect`，签发 `zichan_m`；作为面向普通员工的资产自助门户（有别于 D 级管理员后台 `zichan.ke.com`），进入 `/asset/list` 并调用 `/api/asset/customer/server/v1/asset/inventoryFlag`、`/asset/list`、`/dictionary/getAssetClassification`、`/task/unaccomplished-number`。
- **`contract.ke.com`（法务与业务合同管理）**[P1 · B级]：`cas_redirect`，同时签发 `contract_manage_token` 与 **智能服务簇**（`intelligentService_sid`/`intelligentservice_token`）；侧栏展示 `白名单管理` 与 `欢迎使用合同系统`；本域调用 `/papi/contract/manage/server/v1/auth/info`、`/contract/detail/sug`、`/subject/getServiceSubject`、`/enumeration/getReasonType`，跨模块复用招聘基础库 `/papi/zhaopin/base/server/v1/middle/org/descendant` 与 `/referenceItems`，**并跨站请求公网 `is-prod.ke.com/admin/skill/queryMainPage`**。
- **`workflow.lianjia.com`（通用审批流）**[P2 · B级]：`cas_redirect`（经 `/casLogin`），进入 `/index`（`流程定义列表`），承接老链家通用工作流查询。

> **域 6 C 级边界注记（8 站）**：
> - `finops.ke.com`（费控）与 `rpm.ke.com`（报账）共用 `ecs_console/index.html#/ssoLogin` 架构，调用 `/console/console/v2/sso/querySsoAddress` 时携带签发参数 `loginName=[REDACTED]`/`sign=[REDACTED]`（`rpm` 额外写入 `agentic_token`），在 20 秒观测窗内停留在“加载中”；
> - `bpm.ke.com` 与重定向合流至此的 `bpm-m.ke.com` 均基于 ActionSoft（签发 `AWSSESSIONID`、`aws_uid`），直接访问特定流程启动 URL 提示 `暂时无法访问`；
> - `mdm.ke.com`（主数据）与 `sis.lianjia.com`（SIS 门店损益）CAS 后跳至 `/401`；
> - `m-legal.ke.com`（法螺合同，`investfinance_sid`）与 `vprivacy.ke.com`（数据合规）在 20 秒内陷入渲染超时。

---

## 域 7：产研基建、企业文化与行政服务域

本域共有 **10 站**实质可用（A 级 3 + B 级 7，**全部内网独有**），另有 **2 站 Grade C**（`esghub.ke.com`、`yes-or-no.ke.com`）。全面支撑集团配置下发、云资源账单工单、研发工具与风控治理，以及 IT、行政和 CG 企业文化社区。

### 7.1 `apollo.portal.life.ke.com`（Apollo 配置中心）[P2 · A级 · 内网独有]
- **系统定位**：全生态微服务与多环境（DEV/FAT/UAT/PRO 等）核心配置中心（`title="Apollo Configuration Center"`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `NG_TRANSLATE_LANG_KEY`、`jt_apollo_login_token`。
- **实测 UI 模块**：顶栏 `Help`、`多环境切换`、`Language`。
- **核心后端 API（11 个端到端实测接口）**：`/user`（核验员工身份一致）、`/appnamespaces/public`、`/apps/by-owner`、`/favorites`、`/i18n/en.json`、`/page-settings`、`/permissions/root`、`/prefix-path`、`/system/role/createApplication/[REDACTED]`、`/views/common/footer.html`、`/nav.html`。

### 7.2 `cloud.intra.ke.com`（贝壳云平台 / 产品树）[P2 · A级 · 内网独有]
- **系统定位**：集团自研内网云原生基础设施、产品服务树与云资源费用控制台（`title="贝壳云平台"`，终态 `https://cloud.intra.ke.com/home`）。
- **认证与 Cookie 族**：`cas_redirect`；采用 Node/Egg 会话架构，签发 **7 个 Cookie**：`EGG_SESS`、`cloud_console_token_egg`、`cloud_console_token_egg.sig`、`cloud_console_user_session`、`cloud_console_user_session.sig`、`session`、`session.sig`。
- **实测 UI 模块**：侧栏 `我的工单`、`我的反馈`、`我的费用`。
- **核心后端 API**：`GET /cloud-proxy-api/user/current_user`（`200/200000`，主体核验一致）、`/global_search/history`、`/platform/cloud_product/visit_history`（均 `200/200000`）与 `GET /getRoutersByEnvAndProgramKey`（`200/100000`）。

### 7.3 `wen.ke.com`（贝壳问卷）[P1 · A级 · 内网独有]
- **系统定位**：企业内部调研问卷创建、投放与权限管理平台（`title="贝壳问卷"`，终态 `https://wen.ke.com/home/create`）。
- **认证与 Cookie 族**：`cas_redirect`；签发 `wenjuan_ke_com`。
- **核心后端 API**：`GET /api/taurus/server/v1/flag/admin`、`/v1/user/merchant`（主体核验一致）、`/v2/naire/queryGrayByUser`、`/v2/privilege/ehr/checkByNames`（全 `200`）。

### 7.4–7.10 域 7 其余内网独有 B 级系统（7 站）
- **`easy.intra.lianjia.com`（链家网 EASY 运维工具）**[P2 · B级]：`cas_redirect`（带 `?ticket=[REDACTED]`），签发 `sre_login`；顶栏 `链家网EASY`，侧栏暴露 `Cron任务`、`WORK PROGRESS (5)` 与多组静态示例模板卡片及 `输入PROJECT & TOKEN` 表单。
- **`risk-platform.ke.com`（风控平台）**[P2 · B级]：`cas_redirect`，签发 `lianjia_token`；进入 `/personal` 时跨站对接 `public-risk-control-home.ke.com/login/cas`（`307/302`）与 `/perm/isSuperAdmin`（`200`）。
- **`its.ke.com`（IT 服务 / 视频会议）**与 **`its.lianjia.com`（软件下载）**[均 P1 · B级]：共享 **IT 门户双域名簇** `its_ke_token` 与完全一致的顶栏（`IT服务`、`常见问题`、`软件下载`、`网页邮箱`、`会议预定`）；前者分类指引 `系统类`/`网络类`/`软件类`/`硬件类`/`邮箱类`/`打印机类`（含腾讯会议指引，调用 `/listdata/*` 与 `/user/role`），后者提供内网软件分发库（`A+`、`KeVoiceInput`、`EV邮件归档`、`Office 2019/2021` 等，调用 `/softdata/softlist`）。
- **`homeworld.ke.com`（行政服务平台）**[P1 · B级]：`cas_redirect`，签发 `homeworld_ke_token`；渲染 `行政宝典` 侧栏并调用 `//user/authList` 与 `//user/list`（区别于前端衍生入口 `homeworld-front.ke.com` 被 HIP 拦截）。
- **`mcg.ke.com`（CG 文化圈）**[P2 · B级]：`cas_redirect`，签发 **CG 文化簇** `mcg_ke_lianjia_token`；调用 `/api/v1/activity/push`、`/anniversary/popUp`、`/commitment/popUp`、`/dict`、`/homepage`、`/welfare/monthlyPush` 与 `/api/v2/circle/custom`（`200/CG07732`）。
- **`vote-admin.ke.com`（荣誉评选系统）**[P2 · B级]：`cas_redirect`，签发 **CG 文化簇** `cg_ke_com`；进入 `/pc/values/activityList`，侧栏 `荣誉评选`、`荣誉数据管理`、`首页`、`退出`，调用 `/api/oc/value/vote/server/p/common/v1/perms` 与 `/org/v1/user-roles`（同簇的 `vm.ke.com`、`be-admin.ke.com`、`jingyingshe-admin.ke.com` 在前端权限校验后均为 D 级无权）。

> **域 7 C 级边界注记（2 站）**：`esghub.ke.com`（碳管理平台）签发 `ESGHUB_SESSION`/`esghub_token`/`SESSION_USER`，仅 `get-company-id-by-slug` 返回 `200/0`，其余 5 个身份与业务接口返 `401` 导致页面卡在 `Loading`；`yes-or-no.ke.com`（员工交流平台）签发 `yes-or-no-token` 后 `getUserPerms` 返回 `400/20003` 并跳转 `/p/noPerms`。

---

## 附录：实证跨站 API 调用矩阵摘要

| 被调共享后端 / 能力域 | 暴露边界 | 主调前端系统 | 实测关键路径与架构含义 |
| --- | --- | --- | --- |
| `prd-assistant-backend.ke.com` | 公网/后端网关 | `moma.ke.com`（30）、`app-moma.ke.com`（7） | Moma 对话、模型切换、轻应用与数据资产主后端（`KE-24` CAS 注销脱节点） |
| `smart-mo-api.ke.com` & `smart-mo.ke.com` | 公网 | `moma.ke.com` | 小莫浏览器扩展与桌面宠物伴侣登录桥接（`/api/xiaomo/*`、`/api/browser-extensions/*`） |
| `is-prod.ke.com` | **公网直连可用** | `recruit`、`contract`、`lcm`（B 级）+ `cb`、`leader`（D 级） | 嵌入 8 个内网系统的跨站 AI 技能助手后端（`GET /admin/skill/queryMainPage`） |
| `xiaobei.ke.com` | **公网直连可用** | `shouye.link.lianjia.com`、`link.lianjia.com`（A 级） | 内网工作台首页直接拉取公网小贝知识库登录状态与配置字典 |
| `session-saas-a.*`（`.lianjia` / `.deyoulife` / `.ke`） | 内网 | `shouye`、`link`、`house`、`hetong`、`ke.deyoulife`、`ke.link`、`clue.a` | A+ / 德佑统一导航与三体系经纪人角色切换 API（`/api/getRoleSwitchOptions`） |
| `main.*`（`.link.lianjia` / `.deyoulife`） | 内网 | `shouye`、`link`、`house`、`hetong`、`lease-pz`、`ke.deyoulife`、`ke.link` | 经纪人当前主身份上下文与切换服务（`/broker/change/*`、`/broker/detail/*`） |
| `darius.api.ke.com` & `xinfang.a` <-> `deal.fang` | 内网 | `xinfang.a.ke.com`、`deal.fang.lianjia.com` | 新房 2.0 工作台与成交系统双向互调并共用配置下发接口（`POST /apollo/config`） |
| `compass-server.ke.com` / `ketest.api.ke.com` / `public-risk-control-home.ke.com` | 内网 | 各自同名单页前端 `compass`、`ketest-pro`、`risk-platform` | 独立前端与专有 API/CAS 网关前后端分离对 |
| `rule-chat.ttb.test.ke.com` & `test-login.ke.com` | 测试/预发 | `heimingdan.lianjia.com`（A 级）、`hrlearn.ke.com`（D 级） | **异常信号**：生产系统页面运行时直接请求云内网或测试认证环境接口 |
