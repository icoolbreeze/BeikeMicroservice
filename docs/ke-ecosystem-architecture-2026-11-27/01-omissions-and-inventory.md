# 原有 28 入口遗漏核查、纠偏项与全量分层资产台账

> **唯一数据基线**：本文所有数字严格基于 [final_ecosystem_dataset.json](./final_ecosystem_dataset.json) 生成，全面对比历史只读基线 [summary.json](../crm-sso-entry-review-2026-11-26/summary.json)（28 已确认 + 5 别名 + 15 未计入）与 [kecom-security-audit-report-2026-11-20.md](../kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md)。

---

## 1. 核心对比结论：28 入口遗漏核查、纠偏与净新增

### 1.1 一句话结论（精确对平口径）
- **原有 28 入口实证纠偏后仅剩 24 个实质独立可用业务系统（25 个可用入口主机）**：原 28 入口经真实浏览器深扫复核，发现 **2 站实际无业务权限**（`8.lianjia.com`、`naturaloppty.ke.com`）、**1 站确认为极简认证空壳底座**（`saas.a.ke.com`）、**1 站已重定向合流归并**（`ke.link.lianjia.com` 直接合流至 `ke.deyoulife.com`）。
- **净新增 65 个独立可用业务系统（66 个可用入口主机）**：在原 28 表之外新发现并进入浏览器深扫的 **128** 个内部 Web 站点中，共实证 **66 站**达到实质可用状态（**A 级 21 站 + B 级 45 站**）；扣除其中重定向合流至工作台主站 `shouye.link.lianjia.com` 的 `link.lianjia.com` 后，**净新增独立可用业务系统为 65 个**（另有 14 站处于 C 级部分受限或空壳态）。
- **原有 28 入口真实覆盖率仅 `27.0%`（按入口计 `27.5%`）**：全生态经浏览器实证实质可用的系统总计 **91 个入口主机（38 A + 53 B）**、归并重定向合流后为 **89 个独立业务系统实体**（`24 原有 + 65 净新增 = 89`）。原 28 清单仅覆盖了全生态可用系统实体的 **`24 / 89 = 27.0%`**，遗漏比例高达 **`73.0%`**！

### 1.2 全量数据闭环对账表

| 阶段 / 数据集 | 原始记录数 | 去重后主机数 | 计算关系与闭环说明 |
| --- | ---: | ---: | --- |
| Phase 2A 公网直连（[site_profiles_2a.jsonl](./site_profiles_2a.jsonl)） | 22 | 22 | 5 公网基线 + 11 AI 门户候选 + 3 客服协同 + 3 `page_only` 复现 |
| Phase 2B-1 + P0 内网隧道（[site_profiles_2b.jsonl](./site_profiles_2b.jsonl)） | 72 | 72 | 2B-1（33 站 = 23 原28内网 + 6 2A转入 + 4 2A拦截复核）+ P0（39 站） |
| Phase 2B-P1P2 内网隧道（[site_profiles_2b_p1p2.jsonl](./site_profiles_2b_p1p2.jsonl)） | 72 | 72 | 经 89 站 HTTP 快筛后入围（P1 41 站 + P2 31 站），与前两份零重叠 |
| **Phase 2 独立深扫合计（扣除 10 站双网重测）** | **166** | **156** | `22 + 72 + 72 - 10 = 156`（含 28 个原确认站 + 128 个新补充深扫站） |
| P1/P2 快筛直接淘汰（[phase2b_p1p2_screen.json](./phase2b_p1p2_screen.json)） | 17 | 17 | `denied_403` 3 + `login_required` 7 + `unreachable_or_error` 7 |
| **Phase 2 候选全量闭环** | — | **173** | `156 + 17 = 173` = 原 `28` + Phase1 第1类 `142` + 第3类 `2` + 重测 `app-moma.ke.com` `1` |
| 原 `not_counted` 未重测剩余项 | 15 | **12** | 扣除已入 Phase 2 重测的 3 站（`app-fly`、`jixiao`、`app-moma`）后剩 12 站 |
| 基础设施 / 后端 API / 网关 / 静态域 | — | **30+** | Phase1 第2类 `17` 域 + Phase2 XHR 新挖 `13` 隐性后端域 + 重定向发现底座域 |
| 原 28 入口显式别名域（第4类） | 5 | **5** | `elearning`、`fang.deyoulife`、`helper.lianjia`、`home.ehr.lianjia`、`zhenzhu` |
| 外部第三方 SaaS 与上海德佑品牌域（第5类） | 35 | **35** | 17 公共 AI + 16 第三方办公/福利/招聘 SaaS + 2 德佑 `*.dooioo.com` 入口 |

### 1.3 156 个独立深扫站的七级可用性总分布（A–G）

| 可用等级 | 定义 | 原 28 入口 | 新增 128 站 | 全量 156 站 | 占比 |
| :---: | --- | ---: | ---: | ---: | ---: |
| **A** | 认证通过且完成员工主体核验（`identity_match=true`） | 17 | 21 | **38** | 24.4% |
| **B** | 认证通过/直出且实测可用业务页 | 8 | 45 | **53** | 34.0% |
| **C** | 部分受限、无权提示、未沉降或空壳底座 | 1 | 14 | **15** | 9.6% |
| **D** | 业务侧 RBAC 无权限（403/noPermission/Forbidden） | 2 | 17 | **19** | 12.2% |
| **E** | 边缘安全网关 HIP 拦截（`hip.ke.com` / `hip.lianjia.com`） | 0 | 23 | **23** | 14.7% |
| **F** | 独立认证孤岛 / CAS 回调中断回落登录 | 0 | 3 | **3** | 1.9% |
| **G** | 传输不可达或 HTTP 404/5xx 错误 | 0 | 5 | **5** | 3.2% |
| **合计** | — | **28** | **128** | **156** | **100.0%** |
| **实质可用（A+B）** | **具备完整页面或核心接口可用性** | **25** *(去重 24)* | **66** *(净增 65)* | **91** *(去重 89)* | **58.3%** |

### 1.4 原有 28 入口清单的纠偏明细

历史 `summary.json` 通过轻量 HTTP 探针确认了 28 个主机，本次经全量浏览器深度渲染与 XHR 核验后产生以下 **4 项实质纠偏**：

1. **2 站实测业务无权（从可用降级为 D 级 `denied_403`）**：
   - `8.lianjia.com`（积分提佣）：原记 Grade B；浏览器实测经 CAS 登录后虽 `/session/user`（`identity_match=true`）通过，但 `/calwage/isNewJifen`（`biz_code=2`）判定无权限，自动调用 `/error/unauthorized` 并跳转 `http://8.lianjia.com/pageo/error/unauthorized`。
   - `naturaloppty.ke.com`（二手自然商机展位）：原记 Grade B；浏览器实测跳转合流至 `https://oomp.ke.com/#/naturalOppty/businessopp/focus/overview`（`商机运营管理平台`），调用 `/api/privilege/allRole` 后落入业务无权态（与 P0 候选 `oomp.ke.com` 实为同一系统的两个入口）。
2. **1 站确认为认证底座空壳（从业务入口纠偏为 C 级空壳）**：
   - `saas.a.ke.com`（A+ 客户端认证服务）：原记 Grade B；实测仅返回 902–923 字节极简 HTML（`<title>首页</title>`，0 个 DOM 链接，0 个业务 XHR，仅初始化 `window.__UDL_CONFIG` 并在 `/cas` 换票签发 `SESSION`/`rememberMe`），定位为 A+ 客户端认证底座而非 Web 业务系统。
3. **1 站确认已重定向合流归并（重复实体纠偏）**：
   - `ke.link.lianjia.com`（原记“共享池（旧客源入口）”）：在当前内网浏览器实测下，经 CAS 回调后直接重定向合流至 `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer`，加载的页面 DOM、接口（`ke.deyoulife.com/customer-infrastructure/*`、`main.deyoulife.com`、`session-saas-a.deyoulife.com`）与 `ke.deyoulife.com` 完全一致，不再单独计为独立业务实体。
4. **身份验证升级与默认路径差异**：
   - 原 28 表中此前仅 `8` 站通过 API 验证主体（`Grade A`），本次浏览器深扫在 **17** 个原 28 站中捕获到有效主体核验（新增验证了 `shouye`、`house`、`hetong`、`clue.a`、`ke.deyoulife`、`ke.link`、`trusteeship`、`brand`、`liulian`、`study` 等的 XHR/DOM 主体匹配）。
   - `kemcc.ke.com`（通知中心）根路径默认进入 `/templatesManage`（`getRoleByUcid` 返回 `roles=[]`），但在历史验证路径 `/noticeCenter` 已实证 IM 网关 `imapi.lianjia.com` 身份一致，维持 B 级可用。

---

## 2. 全量分层资产总表（按可用性象限与网络边界）

### 2.1 第一象限：公网直连可用旁路清单（Batch 2A 实证：11 站可用 + 1 站空壳定位）

在完全不接入内网隧道的本机公网环境下，**11 个独立站点实质可用**（7 A + 4 B），另 `app-moma.ke.com` 定位为 C 级部署根空壳：

| # | 主机 (`host`) | 系统名称 | 来源分类 | 终态等级 | 终态 URL / 标题 | SSO 机制 | 核心业务域 |
| ---: | --- | --- | --- | :---: | --- | --- | --- |
| 1 | `helper.ke.com` | 客服中心 / 业绩争议 | 原公网基线 (28) | **A** | `https://helper.ke.com/#/`（`我的客服`） | `cas_redirect` | 客源/触达协同 |
| 2 | `moma.ke.com` | Moma 轻应用平台 | 原公网基线 (28) | **A** | `https://moma.ke.com/nchat`（`MOMA-首页`） | `supabase_cas` | AI 原生与数据智能 |
| 3 | `wandou.ke.com` | 豌豆（CRM 普租 IM） | 原公网基线 (28) | **A** | `https://wandou.ke.com/chat?sessionId=[REDACTED]`（`豌豆 PC`） | `localstorage_bearer` | 客源/触达协同 |
| 4 | `listing-de.ke.com` | 商铺写字楼 | 原公网基线 (28) | **A** | `https://listing-de.ke.com/pc/de/list`（`房源列表`） | `cas_redirect` | 房源/新房/交易 |
| 5 | `letsai.ke.com` | AI 门户 | 原公网基线 (28) | **B** | `https://letsai.ke.com/`（`Let's AI \| AI门户`） | `cas_redirect` | AI 原生与数据智能 |
| 6 | `luoshu.ke.com` | 洛书动态表单 | **新增公网可用** | **A** | `https://luoshu.ke.com/dynamicFormRender?...`（`洛书`） | `cas_redirect` | 客源/触达协同 |
| 7 | `m-jury.ke.com` | B端纠纷调解 | **新增公网可用** | **A** | `https://m-jury.ke.com/new/mediation/agentList` | `cas_redirect` | 客源/触达协同 |
| 8 | `xiaobei.ke.com` | 小贝知道 / 财务指引 | **新增公网可用** | **A** | `https://xiaobei.ke.com/#/`（`帮助`） | `cas_redirect` | 客源/触达协同 |
| 9 | `chatbi.ke.com` | Chat BI | **新增公网可用** | **B** | `https://chatbi.ke.com/`（`Chat BI`，Shadow DOM） | `cas_redirect` | AI 原生与数据智能 |
| 10 | `is-prod.ke.com` | 闹海 / 小伯乐 | **新增公网可用** | **B** | `https://is-prod.ke.com/pages/naohai`（`闹海`） | `cas_redirect` | AI 原生与数据智能 |
| 11 | `aiglasses.ke.com` | 智眸AI眼镜 | **新增公网可用** | **B** | `https://aiglasses.ke.com/`（`智眸 - 镜在眼前，智慧搭伴`） | `none`（免登录直出） | AI 原生与数据智能 |
| *12* | *`app-moma.ke.com`* | *Moma 轻应用托管域* | *`not_counted` 复测* | ***C*** | *跳 `https://moma.ke.com/deploy_404`（`MOMA-页面未找到`）* | *`supabase_cas`* | *需带具体 App UUID 路径* |

> **2A 转入 2B 复测对照（10 站）**：2A 中 `chat-excel.ke.com`、`ketest-pro.ke.com`、`speechhub.ke.com`、`jixiao.ke.com` 4 站在公网 TCP/TLS 建连关闭或空响应，转入 2B 内网后全部转为 **B 级可用**；`codelink.ke.com` 内网返 404（G 级）；`app-fly.ke.com` 与 4 个 2A 公网拦截站（`ai-sec`、`bella`、`jianli`、`opengpt`）在内网隧道下仍 100% 跳转 `hip.ke.com/forbidden`（E 级）。

---

### 2.2 第二象限：内网实证可用系统清单（80 站，与公网 11 站合计 91 站 A+B / 89 独立实体）

按 **7 大业务域**分组展示全部内网实质可用系统（标注原 28 表状态、终态等级、入口 URL 与页面标题）：

#### 域 1：房源、新房与交易核心作业域（内网可用 19 站 + 公网 1 站 = 20 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL / 归并说明 |
| --- | --- | --- | :---: | --- | --- |
| `shouye.link.lianjia.com` | 工作台首页 | 原 28 (2B-1) | **A** | `链家网-Link-首页` | `https://shouye.link.lianjia.com/shou/index`（别名含 `link.lianjia.com`） |
| `link.lianjia.com` | Link（合流工作台） | P0 新增 | **A** | `链家网-Link-首页` | 重定向合流至 `shouye.link.lianjia.com/shou/index` |
| `house.link.lianjia.com` | 房源系统 | 原 28 (2B-1) | **A** | `德佑-房源` | `/search/sale/default/gdiv_mt`（别名 `fang.deyoulife.com`） |
| `trusteeship.link.lianjia.com` | 托管业务系统 | 原 28 (2B-1) | **A** | `首页` | `https://trusteeship.link.lianjia.com/home` |
| `hetong.link.lianjia.com` | 签约合同系统 | 原 28 (2B-1) | **A** | `签约服务-vr` | `https://hetong.link.lianjia.com//index` |
| `link.fang.lianjia.com` | 新房 / A+ 统一顶栏 | 原 28 (2B-1) | **A** | `新房` | `https://link.fang.lianjia.com/map` |
| `storm.link.lianjia.com` | 业绩排名（签中中台） | P0 新增 | **A** | `签中中台` | `https://storm.link.lianjia.com/middle/yeji/index` |
| `xinfang.a.ke.com` | 新房工作台 | P0 新增 | **A** | `新房工作台` | `https://xinfang.a.ke.com/` |
| `deal.fang.lianjia.com` | 新房成交管理系统 | P0 新增 | **A** | `贝壳新房成交系统` | `https://deal.fang.lianjia.com/` |
| `lead.ke.com` | 口袋楼盘 | P0 新增 | **A** | `口袋楼盘` | `https://lead.ke.com/` |
| `lease-pz.link.lianjia.com` | 普租租赁系统 | 原 28 (2B-1) | **B** | `—` | `https://lease-pz.link.lianjia.com/rent/house/list?isSaaS=false` |
| `gte.lianjia.com` | 北京交易系统 | P0 新增 | **B** | `链家网-Link-签后` | `https://gte.lianjia.com/` |
| `intcms.jiaoyi.lianjia.com` | 交易配置平台 | P0 新增 | **B** | `—` | `http://intcms.jiaoyi.lianjia.com/?ticket=[REDACTED]` |
| `user-ntcs.gte.lianjia.com` | 郑州交易可视化系统 | P0 新增 | **B** | `我的交易` | `https://user-ntcs.gte.lianjia.com/list` |
| `cms.newhouse-bd.ke.com` | 客发工作台 | P0 新增 | **B** | `客发CRM项目` | `http://cms.newhouse-bd.ke.com/` |
| `ishop.lianjia.com` | 智慧门店 | P0 新增 | **B** | `地图` | `https://ishop.lianjia.com/admin/area` |
| `ke-map.ke.com` | 壳慧图 | P0 新增 | **B** | `壳慧图` | `https://ke-map.ke.com/admin/home` |
| `loushu.lianjia.com` | 链楼书 | P0 新增 | **B** | `首页` | `https://loushu.lianjia.com/admin/home?...` |
| `mobile.lianjia.com` | LINK-APP | P0 新增 | **B** | `App下载` | `https://mobile.lianjia.com/#/detail/link_secondhand` |

#### 域 2：客源、线索、触达与协同通讯域（内网可用 7 站 + 公网 5 站 = 12 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL / 归并说明 |
| --- | --- | --- | :---: | --- | --- |
| `ke.deyoulife.com` | 德佑客源系统 | 原 28 (2B-1) | **A** | `客源` | `https://ke.deyoulife.com/customer-infrastructure/customer/page/allCustomer` |
| `ke.link.lianjia.com` | 客源系统（合流） | 原 28 (2B-1) | **A** | `客源` | 重定向合流至 `ke.deyoulife.com`（与上项同一实体） |
| `clue.a.ke.com` | 客户公海 / 潜在客户 | 原 28 (2B-1) | **A** | `线索` | `https://clue.a.ke.com/page/thread` |
| `xiaoxi.link.lianjia.com` | 消息系统 | 原 28 (2B-1) | **B** | `链家网-Link-动态` | `https://xiaoxi.link.lianjia.com//trend/index` |
| `kemcc.ke.com` | 通知中心（kemcc） | 原 28 (2B-1) | **B** | `触达平台` | `https://kemcc.ke.com/templatesManage`（含 `/noticeCenter`） |
| `notice.ke.com` | 任务/新个人信息模块 | P0 新增 | **B** | `首页` | `https://notice.ke.com/sasspc/profile` |
| `haoma.lianjia.com` | 免打扰电话查询 | P0 新增 | **B** | `号码通` | `https://haoma.lianjia.com/` |

#### 域 3：商业化展位、规则风控与运营工具域（内网可用 11 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL |
| --- | --- | --- | :---: | --- | --- |
| `brand.ke.com` | 金贝展位推广平台 | 原 28 (2B-1) | **A** | `金贝平台` | `https://brand.ke.com/#/index/home` |
| `liulian.ke.com` | 展位管理后台 | 原 28 (2B-1) | **A** | `留链-展位报买` | `http://liulian.ke.com/static/buy/commentExpo` |
| `b.ke.com` | 币平台 | P0 新增 | **A** | `币平台` | `http://b.ke.com/` |
| `wrule.ke.com` | 贝壳规则 | P0 新增 | **A** | `贝壳规则` | `https://wrule.ke.com/#/` |
| `heimingdan.lianjia.com` | 黑名单 | P0 新增 | **A** | `贝壳规则` | `http://heimingdan.lianjia.com/#/` |
| `limu.ke.com` | 立木设计平台 | P0 新增 | **A** | `为你推荐` | `https://limu.ke.com/recommend` |
| `content.lianjia.com` | 便民服务 | P0 新增 | **A** | `国民链家后台` | `https://content.lianjia.com/service/order/print` |
| `newhouse-cps.ke.com` | 商圈房源聚焦 | 原 28 (2B-1) | **B** | `配置管理系统` | `https://newhouse-cps.ke.com/grid/focus/admineActivity` |
| `rulemanage.ke.com` | 同业经纪人查询 | P0 新增 | **B** | `蓝鲸` | `https://rulemanage.ke.com/#/widget/common/black/list` |
| `flint.lianjia.com` | 火石作业物料 | P0 新增 | **B** | `火石物料首页` | `https://flint.lianjia.com/?from=link_home` |
| `cms-tv.ke.com` | 门店培训大屏内容系统 | P0 新增 | **B** | `门店培训大屏-内容管理系统` | `https://cms-tv.ke.com/carousel/new` |

#### 域 4：AI 原生平台、数据智能与分析域（内网可用 9 站 + 公网 5 站 = 14 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL |
| --- | --- | --- | :---: | --- | --- |
| `odin.ke.com` | Odin 数据平台 / 驾驶舱 | 原 28 (2B-1) | **A** | `奥丁数据分析平台` | `https://odin.ke.com/` |
| `codin.ke.com` | CHO 驾驶舱（Odin 族） | P1 新增 | **A** | `奥丁数据分析平台` | `https://codin.ke.com/portal/3956/49460` |
| `chat-excel.ke.com` | CHAT-EXCEL | 2A→2B-1 | **B** | `CHAT-EXCEL` | `https://chat-excel.ke.com/` |
| `ketest-pro.ke.com` | Ketest Copilot | 2A→2B-1 | **B** | `ketest一站式质量平台` | `https://ketest-pro.ke.com/#/auth?...` |
| `speechhub.ke.com` | 贝壳语音 | 2A→2B-1 | **B** | `SpeechHub · 贝壳语音算法团队` | `http://speechhub.ke.com/` |
| `biz-data.ke.com` | 业务事件平台 | P0 新增 | **B** | `业务事件平台` | `https://biz-data.ke.com/event` |
| `data.ke.com` | 新房业务、每日关注 | P0 新增 | **B** | `看板` | `https://data.ke.com/` |
| `fast.ke.com` | 天眼系统（FAST） | P0 新增 | **B** | `FAST` | `http://fast.ke.com/` |
| `compass.ke.com` | 埋点平台 / 罗盘 | P2 新增 | **B** | `罗盘` | `http://compass.ke.com/` |

#### 域 5：员工门户、HR 人事、考勤薪酬与培训绩效域（内网可用 16 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL |
| --- | --- | --- | :---: | --- | --- |
| `family.ke.com` | 员工门户 | 原 28 (2B-1) | **A** | `贝壳` | `https://family.ke.com/home/portal`（别名 `home.ehr.lianjia.com`） |
| `study.ke.com` | 贝壳学堂 / 贝好学 | 原 28 (2B-1) | **A** | `在线课堂` | `https://study.ke.com/courseList`（别名 `elearning.ke.com`） |
| `nltx.ke.com` | 职级平台 | P2 新增 | **A** | `积分职级` | `https://nltx.ke.com/pageo/integral-detail` |
| `m-intelligencecampus-hq.ke.com` | 老左图书馆 | P2 新增 | **A** | `老左图书馆` | `https://m-intelligencecampus-hq.ke.com/LibraryBooking/bookingIndex` |
| `hr.ke.com` | HR 门户（KeHR） | 原 28 (2B-1) | **B** | `KeHR` | `https://hr.ke.com/home/index` |
| `kaoqin.hr.ke.com` | 考勤 SAAS 系统 | 原 28 (2B-1) | **B** | `考勤SAAS系统` | `http://kaoqin.hr.ke.com/attendance/calendar` |
| `xinchou.ke.com` | 薪酬核算系统 | 原 28 (2B-1) | **B** | `薪酬管理系统` | `https://xinchou.ke.com/#/` |
| `jixiao.ke.com` | 绩效与试用期系统 | 2A→2B-1 | **B** | `职能绩效系统` | `https://jixiao.ke.com/p/home` |
| `recruit.ke.com` | 招聘 / 入职管理 | P1 新增 | **B** | `贝壳招聘` | `https://recruit.ke.com/application-zhaopin-datacenter` |
| `profile.ke.com` | 人才管理 | P1 新增 | **B** | `贝壳人才管理系统` | `https://profile.ke.com/talent` |
| `resume-agc.ke.com` | 聘多多（内网入口） | P2 新增 | **B** | `聘多多` | `https://resume-agc.ke.com/talent/candidateList` |
| `salary-middle.ke.com` | 薪酬中心 | P2 新增 | **B** | `薪酬中心` | `https://salary-middle.ke.com/` |
| `salary-tax.ke.com` | 税友平台 | P2 新增 | **B** | `税友中心` | `https://salary-tax.ke.com/` |
| `salary-monitor.ke.com` | 服务监控 | P2 新增 | **B** | `服务监控` | `https://salary-monitor.ke.com/p/home` |
| `lcm.ke.com` | 预实管理 | P2 新增 | **B** | `预实管理系统` | `https://lcm.ke.com/` |
| `admin-huaqiao.ke.com` | 花桥学堂 CMS | P2 新增 | **B** | `花桥学堂CMS` | `https://admin-huaqiao.ke.com/` |

#### 域 6：财务费控、资产、法务合规与审批中台域（内网可用 8 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL |
| --- | --- | --- | :---: | --- | --- |
| `budget-control.ke.com` | 预算管控系统 | P1 新增 | **A** | `预算管控系统` | `http://budget-control.ke.com/#/` |
| `mknow.ke.com` | 违纪通报 | P1 新增 | **A** | `知识列表` | `https://mknow.ke.com/#/list/28981?...` |
| `bfa.ke.com` | 财务+（7 大子平台入口） | P1 新增 | **B** | `财务` | `https://bfa.ke.com/` |
| `pay.ke.com` | Kepay 报销平台 | P1 新增 | **B** | `贝壳Pay` | `https://pay.ke.com/p/` |
| `kepler.ke.com` | 税务平台 | P1 新增 | **B** | `税务平台` | `https://kepler.ke.com/invoice-apply` |
| `mzichan.ke.com` | 我的资产 / 资产申请 | P1 新增 | **B** | `我的资产` | `https://mzichan.ke.com/asset/list` |
| `contract.ke.com` | 合同管理 | P1 新增 | **B** | `合同管理` | `https://contract.ke.com/` |
| `workflow.lianjia.com` | 通用审批流 | P2 新增 | **B** | `流程定义列表` | `http://workflow.lianjia.com/index` |

#### 域 7：产研基建、企业文化与行政服务域（内网可用 10 站）
| 主机 (`host`) | 系统名称 | 来源 / 批次 | 等级 | 页面标题 | 终态 URL |
| --- | --- | --- | :---: | --- | --- |
| `apollo.portal.life.ke.com` | Apollo 配置中心 | P2 新增 | **A** | `Apollo Configuration Center` | `http://apollo.portal.life.ke.com/` |
| `cloud.intra.ke.com` | 贝壳云 / 产品树 | P2 新增 | **A** | `贝壳云平台` | `https://cloud.intra.ke.com/home` |
| `wen.ke.com` | 贝壳问卷 | P1 新增 | **A** | `贝壳问卷` | `https://wen.ke.com/home/create` |
| `easy.intra.lianjia.com` | 链家网 EASY | P2 新增 | **B** | `链家网EASY` | `http://easy.intra.lianjia.com/?ticket=[REDACTED]` |
| `risk-platform.ke.com` | 风控平台 | P2 新增 | **B** | `—` | `https://risk-platform.ke.com/personal` |
| `its.ke.com` | IT 服务 / 视频会议 | P1 新增 | **B** | `IT门户` | `https://its.ke.com/article/articledetail?cid=9&aid=80` |
| `its.lianjia.com` | 软件下载 | P1 新增 | **B** | `IT门户` | `https://its.lianjia.com/software/windows` |
| `homeworld.ke.com` | 行政服务平台 | P1 新增 | **B** | `行政服务平台` | `https://homeworld.ke.com/` |
| `mcg.ke.com` | CG 文化圈 | P2 新增 | **B** | `贝壳CG` | `https://mcg.ke.com/?tab=homePage` |
| `vote-admin.ke.com` | 荣誉评选系统 | P2 新增 | **B** | `活动列表 - 荣誉系统` | `https://vote-admin.ke.com/pc/values/activityList` |

---

### 2.3 核心后端 API 网关 / 轻应用运行时 / 支撑底座清单

汇总历史审计文档与 Phase 1（17 域）+ 本轮 156 站 XHR/重定向流量中新挖出的 **13 个隐性后端域**及基础设施网关：

| 类别 | 主机 (`host`) | 来源 | 关联前端 / 调用方 | 架构角色与观测接口 |
| --- | --- | --- | --- | --- |
| **统一认证与安全网关** | `login.ke.com` | 历史审计/第2类 | 全生态 120+ 站 | 集团统一 CAS 认证中心（`/login`） |
| | `hip.ke.com`、`hip.lianjia.com` | Phase 2 重定向 | 23 个被拦截站点 | 边缘零信任安全网关（`/forbidden`） |
| | `app-prd.ke.com` | Phase 2 重定向 | `app-fly.ke.com`（及历史审计 GoTrue 链） | Supabase/GoTrue 认证桥接域 |
| | `saas.a.ke.com` | 原 28 (C 级) | A+ 桌面端 | A+ 客户端 CAS 票据交换底座 |
| **A+ 核心作业中台网关** | `session-saas-a.lianjia.com` | Phase 1 第2类 | `shouye`、`link`、`house`、`hetong` | 统一导航与经纪人角色切换（`/api/navigation`、`/api/getRoleSwitchOptions`） |
| | `session-saas-a.deyoulife.com` | **本轮 XHR 新挖** | `ke.deyoulife.com`、`ke.link.lianjia.com` | 德佑体系角色切换 API（`/api/getRoleSwitchOptions`） |
| | `session-saas-a.ke.com` | **本轮 XHR 新挖** | `clue.a.ke.com` | 线索体系角色切换 API（`/api/getRoleSwitchOptions`） |
| | `main.link.lianjia.com` | Phase 1 第2类 | `shouye`、`link`、`house`、`hetong`、`lease-pz` | 经纪人身份上下文与切换网关（`/broker/change/[REDACTED]/`、`/broker/detail/[REDACTED]/`） |
| | `main.deyoulife.com` | **本轮 XHR 新挖** | `ke.deyoulife.com`、`ke.link.lianjia.com` | 德佑经纪人身份上下文网关（`/broker/change/[REDACTED]/`） |
| **新房与业务微服务网关** | `darius.api.ke.com` | **本轮 XHR 新挖** | `xinfang.a.ke.com`、`deal.fang.lianjia.com` | 新房共享配置网关（`POST /apollo/config`） |
| | `i-wp.ke.com` | **本轮 XHR 新挖** | `xfp.ke.com` | 新房业绩计佣微服务（`/ljzc/xf/performance/server/*`） |
| | `secondx.data.lianjia.com` | Phase 2 重定向 | `fast.ke.com` | 天眼系统认证与 `_secondx` 会话签发域 |
| **AI / 轻应用与智能服务网关** | `prd-assistant-backend.ke.com` | 历史审计/第2类 | `moma.ke.com`、`app-moma.ke.com` | Moma 业务主后端（40+ API 端点） |
| | `smart-mo-api.ke.com`、`smart-mo.ke.com` | 历史审计+Phase 2 | `moma.ke.com` | 小莫宠物/扩展登录与会话网关（`/api/xiaomo/*`） |
| | `is-prod.ke.com` | **本轮跨站新挖** | `hr`、`hrlearn`、`recruit`、`contract`、`cb`、`lcm`、`leader` | 嵌入式智能技能助手主后端（`/admin/skill/queryMainPage`） |
| | `ai-security-data.ke.com` | **本轮 XHR 新挖** | `app-fly.ke.com` | AI 安全数据采集网关（`POST /api/v1/collect/report`） |
| | `moma-data.ke.com`、`flow-forge.ke.com`、`linkspace.ke.com` | 历史审计/第2类 | 轻应用/工作流生态 | Moma 数据、流程编排与空间关联基础设施 |
| **IM / 推送 / 产研专业后端** | `imapi.lianjia.com` | 历史审计/第2类 | `kemcc.ke.com` | IM 消息与会话同步网关（`/user/detail/fetch`、`/msg/sync`） |
| | `msg.ke.com` | Phase 1 第2类 | `family.ke.com` | 员工门户 WebPush 推送网关（`/sound/wave/webpush/server/v1/notice`） |
| | `wxwork.ke.com` | 历史审计/第2类 | `family.ke.com` 等企微入口 | 企业微信 OAuth 回调认证域 |
| | `compass-server.ke.com` | **本轮 XHR 新挖** | `compass.ke.com` | 罗盘埋点平台 CAS 与后端服务域 |
| | `ketest.api.ke.com` | **本轮 XHR 新挖** | `ketest-pro.ke.com` | Ketest 质量平台认证与接口网关（`/authagent-api/*`） |
| | `public-risk-control-home.ke.com` | **本轮 XHR 新挖** | `risk-platform.ke.com` | 风控平台权限后端（`/login/cas`、`/perm/isSuperAdmin`） |
| **测试 / 预发与异常信号域** | `*.ttb.test.ke.com`、`rule-chat.ttb.test.ke.com` | 审计+本轮 XHR | `heimingdan.lianjia.com`（异常生产调用） | 云内网 RFC 6598 测试环境（生产页面直接请求 `/api/chat/fixed_script`） |
| | `test-login.ke.com` | **本轮 XHR 新挖** | `hrlearn.ke.com`（异常生产调用） | 测试认证域（生产域请求 `/authentication/sdk/init`） |
| **CDN / 埋点与外部 API** | `img.ljcdn.com`、`file.ljcdn.com`、`ods.lianjia.com` | 历史审计/第2类 | 全站图片/附件/对象存储 | 图片 CDN、文件下载与对象存储预签名宿主 |
| | `public-digc.ke.com`、`public-h5-be-report.ke.com` | Phase 1 第2类 | 全站前端埋点 | 集团统一行为上报与前端埋点网关 |
| | `restapi.amap.com` | **本轮 XHR 新挖** | `lead.ke.com` | 外部高德地图 SDK 服务接口 |

---

### 2.4 第四象限：不可用与受限系统边界全量台账

#### A. Grade C：部分受限、无权提示、未沉降或空壳底座（15 站）
- **明确无权 / 401 / 错误提示（7 站）**：`hx.ke.com`（`/notauth`）、`xfp.ke.com`（`title=错误`）、`yes-or-no.ke.com`（`/p/noPerms`，`400/20003`）、`mdm.ke.com`（`/401`）、`sis.lianjia.com`（`/401`）、`bpm.ke.com`（`暂时无法访问`）、`bpm-m.ke.com`（跳 `bpm.ke.com` `暂时无法访问`）。
- **认证 / 部署空壳（2 站）**：`saas.a.ke.com`（A+ 客户端 CAS 票据交换空壳）、`app-moma.ke.com`（跳 `moma.ke.com/deploy_404`）。
- **API 401 / 超时 / 二次 SSO 加载滞留（6 站）**：`esghub.ke.com`（5 个业务 API 返 401）、`shebao.ke.com`（`currUser` 返 401）、`vprivacy.ke.com`（20s 超时且 `sso=none`）、`m-legal.ke.com`（20s 超时）、`finops.ke.com`（滞留 `#/ssoLogin` 加载中）、`rpm.ke.com`（滞留 `#/ssoLogin` 加载中）。

#### B. Grade E：边缘安全网关 HIP 拦截（23 站）
- **公网 + 内网双网实测均被 `hip.ke.com` 拦截（5 站）**：`ai-sec.ke.com`、`bella.ke.com`、`jianli.ke.com`、`opengpt.ke.com`、`app-fly.ke.com`（2A 直连关闭，2B 过 CAS/Supabase 后仍落 HIP）。
- **P0 内网被 `hip.lianjia.com` 拦截（2 站）**：`pan.fang.lianjia.com`、`newcms.fang.lianjia.com`。
- **P0 内网被 `hip.ke.com` 拦截（2 站）**：`e-contract.ke.com`、`lingjing.ke.com`。
- **P1/P2 内网被 `hip.ke.com` 拦截（14 站）**：`okr.ke.com`、`ones.ke.com`、`report.ke.com`、`meeting-assistant.ke.com`、`profile-h5.ke.com`、`inrecruit.ke.com`、`spi.ke.com`、`chip.ke.com`、`legal-compliance.ke.com`、`case-legal.ke.com`、`company-legal.ke.com`、`homeworld-front.ke.com`、`ganbu.ke.com`、`gongyi.ke.com`。

#### C. Grade D：业务侧 RBAC 无权（19 站，外加 P1/P2 快筛淘汰 3 站 = 22 站）
- **原 28 纠偏（2 站）**：`8.lianjia.com`（`/pageo/error/unauthorized`）、`naturaloppty.ke.com`（合流至 `oomp.ke.com`）。
- **P0（4 站）**：`oomp.ke.com`、`price.lianjia.com`（`/fee/forbidden`，`biz_code=-99990`）、`morg.lianjia.com`（`/error/noPermission`）、`wi.ke.com`。
- **P1/P2 深扫（13 站）**：`renshi.ehr.lianjia.com`、`caigou.ke.com`、`zichan.ke.com`、`shandian.lianjia.com`、`xinchou-data.ke.com`、`cb.ke.com`、`leader.ke.com`、`hrlearn.ke.com`、`vm.ke.com`、`admin-study.ke.com`、`be-admin.ke.com`、`jingyingshe-admin.ke.com`、`kehome-cms.ke.com`。
- **P1/P2 快筛已淘汰（3 站）**：`ketalk.ke.com`（403）、`admin.uc.lianjia.com`（`/noauth`）、`ireport.lianjia.com`（`/401`）。

#### D. Grade F：独立认证孤岛 / 未完成认证（3 站深扫 + 7 站快筛淘汰 = 10 站）
- **深扫实证（3 站）**：`admin-sunshine.ke.com`（`#/login` 独立登录）、`fssc.lianjia.com`（`登陆失败提示`）、`new-meeting.ke.com`（`/proxy4ke/login/cas` 中断回落 `/login`）。
- **快筛淘汰（7 站）**：`mail.ke.com`（Outlook）、`owa.lianjia.com`（Outlook）、`wiki.lianjia.com`（Wiki 独立登录）、`dawei.ke.com`（大为专利独立登录）、`weapons.ke.com`（落兵台循环重定向）、`hecapp.homelink.com.cn`（AFA 爱发独立登录）、`zhidao.ke.com`（`zlogin.ke.com` 独立登录）。

#### E. Grade G：传输不可达 / 退役 404（5 站深扫 + 7 站快筛淘汰 = 12 站）
- **深扫实证（5 站）**：`codelink.ke.com`（404）、`legal-lawyer-library.ke.com`（`/callback` 404）、`yezhu.link.lianjia.com`（`ERR_CONNECTION_CLOSED`）、`ncrm.lianjia.com`（`ERR_SOCKS_CONNECTION_FAILED`）、`pref.merlin.lianjia.com`（`ERR_SOCKS_CONNECTION_FAILED`）。
- **快筛淘汰（7 站）**：`yunpan.lianjia.com`（302 异常）、`zjjg.homelink.com.cn`（连接断开）、`design.ke.com`（404）、`pm.ke.com`（TLS EOF）、`recruit-ops.ke.com`（畸形响应）、`solicitude.hr.lianjia.com`（404）、`gongwei.ke.com`（404）。

#### F. 历史 `not_counted` 未重测 12 站与第 4 类 5 别名 + 第 5 类 35 外部 SaaS/品牌域
- **原 `not_counted` 12 个未重测主机**（其余 3 个 `app-fly`、`jixiao`、`app-moma` 已并入 Phase 2）：`aplus-im.ke.com`（DNS NXDOMAIN）、`hestia.lianjia.com`（HIP `forbidden`）、`i.nts.jiaoyi.ke.com`（403 无权限）、`rfq.ke.com`（403）、`lj.lianjia.com`（`login.lianjia.com` 独立域）、`reds.lianjia.com`（`auth.lianjia.com`）、`sm.lianjia.com`（独立登录）、`databoard.lianjia.com`（TLS EOF）、`mail.homelink.com.cn`（证书不匹配）、`te.lianjia.com`（502）、`xss.lianjia.com`（连接断开）、`jiayou.lianjia.com`（外链跳转 `www.jiayou321.com`）。
- **第 4 类 5 个别名域**：`elearning.ke.com` → `study.ke.com`、`fang.deyoulife.com` → `house.link.lianjia.com`、`helper.lianjia.com` → `helper.ke.com`、`home.ehr.lianjia.com` → `family.ke.com`、`zhenzhu.ke.com` → `listing-de.ke.com`。
- **第 5 类 35 个外部 SaaS 与关联品牌域**：
  - **17 个外部 AI 与大模型**：`agent.minimaxi.com`、`ai.wps.cn`、`app.napkin.ai`、`chat.deepseek.com`、`chatglm.cn`、`doc.weixin.qq.com`、`hr.nowcoder.com`、`jimeng.jianying.com`、`kimi.moonshot.cn`、`meeting.tencent.com`、`miaohua.sensetime.com`、`notebooklm.google.com`、`passport.xfyun.cn`、`tongyi.aliyun.com`、`www.coze.cn`、`yiyan.baidu.com`、`yuanbao.tencent.com`。
  - **16 个第三方企业 SaaS / 福利 / 地图**：`365.kdocs.cn`、`brizoo.eagleyun.com`、`h5.keyoucai.com`、`jkb.sipaicare.com.cn`、`lianjia.yunxuetang.cn`、`map.baidu.com`、`miao.baidu.com`、`neitui.italent.cn`、`neitui.zhiye.com`、`tms.beisen.com`、`www.italent.cn`、`open.weixin.qq.com`、`work.weixin.qq.com`、`www.beike.firacademy.com`、`www.huzhujin.com`、`www.jiayou321.com`。
  - **2 个上海德佑独立认证与人资集群入口（`*.dooioo.com`）**：`login.dooioo.com`（CAS 回调透出 `xfpingtai.dooioo.com`、`jiaoyi.dooioo.com`、`jinrong.dooioo.com`、`zzjg.dooioo.com`、`guanggao.dooioo.com`、`wenshu.dooioo.com`、`shuifei.dooioo.com`、`shluyin.dooioo.com` 等 9 个上海业务子域）与 `renzi.dooioo.com`（上海德佑人资系统）。
