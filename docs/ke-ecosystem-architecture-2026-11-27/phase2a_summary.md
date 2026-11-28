# Phase 2A 公网直连测绘报告（Batch 2A，22 站）

> **执行模式**：本机公网直连（`network_mode=direct_local`，未启用 SOCKS5 隧道），`--timeout-ms 25000`，单站独立隔离 `BrowserContext`，全局拦截 `/(logout|signout|logoff)(/|$|\?)`（全程 `0` 次触发退出），内存读取会话凭证不落盘。
> **核心结论**：Batch 2A 共 **22** 站全部完成测绘与逐行落盘（`site_profiles_2a.jsonl`）。**5 个公网基线站 100% 认证通过**（未触发连续 3 站登录回落熔断）；另外新确认 **6 个候选站点公网直连可用**（含 3 个客服协同系统全量通过主体核验、3 个 AI/智能体系统公网可用），`app-moma.ke.com` 确认跳转到 `moma.ke.com/deploy_404` 空应用路径，**4 站**触发贝壳公网 `hip.ke.com/forbidden` 安全拦截（`denied_403`），**6 站**因公网 TCP/TLS 建连关闭或空响应无法直连，已自动写入 [phase2a_handoff_to_2b.json](./phase2a_handoff_to_2b.json) 转入 Phase 2B 内网隧道复测。

---

## 1. 22 站公网直连测绘结果分布

| 结论分类 (`access_verdict`) | 数量 | 占比 | 涉及站点 |
| --- | ---: | ---: | --- |
| `authenticated_verified`（认证通过且主体核验一致） | **7** | 31.8% | 基线 4 站（`helper.ke.com`、`moma.ke.com`、`wandou.ke.com`、`listing-de.ke.com`）+ 客服协同 3 站（`luoshu.ke.com`、`m-jury.ke.com`、`xiaobei.ke.com`） |
| `authenticated_page`（页面 HTTP 200 可达且已登录/公开可访问，未命中个人主体字段或落入部署 404 页） | **5** | 22.7% | 基线 1 站（`letsai.ke.com`）+ AI 候选 3 站（`aiglasses.ke.com`、`chatbi.ke.com`、`is-prod.ke.com`）+ `app-moma.ke.com`（跳至 `moma.ke.com/deploy_404`） |
| `denied_403`（被边缘安全网关拦截） | **4** | 18.2% | `ai-sec.ke.com`、`bella.ke.com`、`jianli.ke.com`、`opengpt.ke.com`（均跳转 `hip.ke.com/forbidden`） |
| `login_required`（回落登录页） | **0** | 0.0% | 无 |
| `direct_blocked_handoff_2b`（直连建连失败，转入 2B） | **6** | 27.3% | `chat-excel.ke.com`、`codelink.ke.com`、`ketest-pro.ke.com`、`speechhub.ke.com`、`app-fly.ke.com`、`jixiao.ke.com` |
| `error` | **0** | 0.0% | 无 |
| **合计** | **22** | **100%** | — |

---

## 2. 22 站逐站可达性与认证结论总表

| # | 主机 (`host`) | 系统名称 | 来源分类 (`category`) | 装配模式 | HTTP | 终态 URL | SSO 类型 | 判定 (`access_verdict`) | 主体核验 | API 域数 / 接口数 | 耗时 |
| ---: | --- | --- | --- | --- | ---: | --- | --- | --- | :---: | :---: | ---: |
| 1 | `helper.ke.com` | 客服中心 / 业绩争议 | `baseline_public_direct` | `cas_cookie` | 200 | `https://helper.ke.com/#/` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 10 | 5.75s |
| 2 | `letsai.ke.com` | AI 门户 | `baseline_public_direct` | `cas_cookie` | 200 | `https://letsai.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 1 / 1 | 6.08s |
| 3 | `moma.ke.com` | Moma 轻应用平台 | `baseline_public_direct` | `moma_supabase_cas` | 200 | `https://moma.ke.com/nchat` | `supabase_cas` | `authenticated_verified` | `true` | 5 / 41 | 9.53s |
| 4 | `wandou.ke.com` | 豌豆（CRM 普租 IM） | `baseline_public_direct` | `wandou_localstorage_token` | 200 | `https://wandou.ke.com/chat?sessionId=[REDACTED]` | `localstorage_bearer` | `authenticated_verified` | `true` | 1 / 8 | 8.64s |
| 5 | `listing-de.ke.com` | 商铺写字楼 | `baseline_public_direct` | `cas_cookie` | 200 | `https://listing-de.ke.com/pc/de/list` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 14 | 7.46s |
| 6 | `ai-sec.ke.com` | 贝壳安全AI助手 | `ai_portal_candidate` | `cas_cookie` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.30s |
| 7 | `aiglasses.ke.com` | 智眸AI眼镜 | `ai_portal_candidate` | `cas_cookie` | 200 | `https://aiglasses.ke.com/` | `none` | `authenticated_page` | `false` | 0 / 0 | 4.15s |
| 8 | `bella.ke.com` | Bella（招聘与面试 AI 助手） | `ai_portal_candidate` | `cas_cookie` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.20s |
| 9 | `chat-excel.ke.com` | CHAT-EXCEL | `ai_portal_candidate` | `cas_cookie` | — | `https://chat-excel.ke.com/` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.13s |
| 10 | `chatbi.ke.com` | Chat BI | `ai_portal_candidate` | `cas_cookie` | 200 | `https://chatbi.ke.com/` | `cas_redirect` | `authenticated_page` | `false` | 2 / 7 | 7.45s |
| 11 | `codelink.ke.com` | CodeLink | `ai_portal_candidate` | `cas_cookie` | — | `https://codelink.ke.com/` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.12s |
| 12 | `is-prod.ke.com` | 闹海 / 小伯乐 | `ai_portal_candidate` | `cas_cookie` | 200 | `https://is-prod.ke.com/pages/naohai` | `cas_redirect` | `authenticated_page` | `false` | 2 / 2 | 4.44s |
| 13 | `jianli.ke.com` | 聘多多系统 | `ai_portal_candidate` | `cas_cookie` | 200 | `https://hip.ke.com/forbidden?...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.23s |
| 14 | `ketest-pro.ke.com` | Ketest Copilot | `ai_portal_candidate` | `cas_cookie` | — | `https://ketest-pro.ke.com/#/keTest-copilot` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.15s |
| 15 | `opengpt.ke.com` | ChatGPT 4 | `ai_portal_candidate` | `cas_cookie` | 200 | `https://hip.ke.com/forbidden?...#/intelligentChat/...` | `unknown` | `denied_403` | `false` | 0 / 0 | 3.22s |
| 16 | `speechhub.ke.com` | 贝壳语音 | `ai_portal_candidate` | `cas_cookie` | — | `http://speechhub.ke.com/` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.11s |
| 17 | `luoshu.ke.com` | 洛书动态表单（首看保护/印章/访客/干部） | `help_center_related` | `cas_cookie` | 200 | `https://luoshu.ke.com/dynamicFormRender?...` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 6 | 6.36s |
| 18 | `m-jury.ke.com` | B端纠纷调解 | `help_center_related` | `cas_cookie` | 200 | `https://m-jury.ke.com/new/mediation/agentList` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 4 | 4.68s |
| 19 | `xiaobei.ke.com` | 小贝知道 / 财务制度&财务指引 | `help_center_related` | `cas_cookie` | 200 | `https://xiaobei.ke.com/#/` | `cas_redirect` | `authenticated_verified` | `true` | 2 / 11 | 6.53s |
| 20 | `app-fly.ke.com` | 员工文化应用 | `reappeared_page_only_unverified` | `cas_cookie` | — | `https://app-fly.ke.com/employee-relations-hub` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.08s |
| 21 | `jixiao.ke.com` | 绩效与试用期系统 | `reappeared_page_only_unverified` | `cas_cookie` | — | `https://jixiao.ke.com/p/home` | `unknown` | `direct_blocked_handoff_2b` | `false` | 0 / 0 | 10.11s |
| 22 | `app-moma.ke.com` | Moma 轻应用托管域 | `reappeared_page_only_unverified` | `moma_supabase_cas` | 200 | `https://moma.ke.com/deploy_404` | `supabase_cas` | `authenticated_page` | `false` | 1 / 7 | 5.62s |

---

## 3. 公网可用站点核心功能模块与后端 API 域解析

### 3.1 5 个已验证公网基线站点
1. **`helper.ke.com`（客服中心 / 业绩争议，`authenticated_verified`）**
   - **核心模块**：`客服中心`、`首页`、`我的反馈`、`消息`、`常用服务`、`常见问题`、`为你推荐`、`联系我们`。
   - **认证与后端 API 域**：经 `login.ke.com/login` 签发 `SESSION`；主要业务接口位于 `helper.ke.com`（`/web/loginUser/info`、`/web/fe/helpcenter/moduleConfigServiceImpl/getHomePageConfig`、`/web/fe/knowledge/knowledgeServiceImpl/queryHotTagForC`、`/web/fe/knowledge/knowledgeServiceImpl/queryKnowledgeListForC`、`/web/fe/message/innerMailServiceImpl/help/getUnReadCount`、`/web/fe/message/noticeServiceImpl/getOnlineNotice`）。
2. **`letsai.ke.com`（AI 门户，`authenticated_page`）**
   - **核心模块**：WordPress OneNav 导航架构，包含 `内部应用专区`、`AI模型应用`、`AI办公工具`、`AI图像工具`、`AI公开课`、`Vibe Working实践案例`、`人财法工具专区`、`AI工具教程`、`AI实践分享`、`我要投稿`。
   - **认证与后端 API 域**：经 `login.ke.com/login` -> `/wp-login.php` CAS 回调签发 `PHPSESSID` 与 `wordpress_logged_in_*` / `wordpress_sec_*`，页面直出渲染无额外 XHR。
3. **`moma.ke.com`（Moma 轻应用与 AI 工作台，`authenticated_verified`）**
   - **核心模块**：登录后落入 `/nchat`（`MOMA-首页`），左侧与功能域包含 `新建 New`、`发现 Discover`、`应用 Apps`、`专家 Experts`、`技能 Skills`、`空间 Space`、`数据 Data`、`连接 Connectors`、`计划 Plan`、`任务助手`、`应用构建`，主页展示各业务与 AI 团队应用卡片。
   - **认证与后端 API 域**：仅带 `TGC`/`TGC_Secure` 访问即自动通过 `login.ke.com/login` 完成 CAS 换票，并签发 `prd-assistant-token-prod`、`X_Access_Token`、`smart_mo_admin_session`、`session_id`；观测到 **41 个**接口调用横跨 5 个主机：
     - `moma.ke.com`（`/skill-hub/api/v1/session/app-link-list`、`/skill-hub/api/v1/user-skill/all-installed`）
     - `prd-assistant-backend.ke.com`（`/api/user/info`、`/api/user/check-auth`、`/api/chats`、`/api/chats/lite_app/mention/user_resource`、`/api/gateway/agi-agent/v1/*`、`/api/gateway/agi-agent/v2/agent-flash/access`、`/api/moma-data/data-assets/accessible`、`/api/homepage-cards`、`/api/partner-community/summary`、`/api/partner-showcase/cards`、`/api/recent-artifacts`、`/api/v1/switchable-models`）
     - `smart-mo-api.ke.com` & `smart-mo.ke.com`（`/api/auth/callback`、`/api/xiaomo/auth/login`、`/api/xiaomo/pet/bootstrap`、`/api/browser-extensions/current`）
     - `login.ke.com`
4. **`wandou.ke.com`（豌豆 CRM 普租 IM / AI 工作台，`authenticated_verified`）**
   - **核心模块**：仅向该 origin 注入 `localStorage.token` 后自动跳转 `/chat?sessionId=[REDACTED]`（`豌豆 PC`），左侧模块包含 `对话`、`笔记`、`知识库`、`AI分身`。
   - **认证与后端 API 域**：`localstorage_bearer` 模式，调用 `wandou.ke.com` 本域接口：`GET /api/v2/users/me`（`biz_code=0` 验证身份一致）、`/api/v2/notes/business-config`、`/api/v2/personal-skills`、`/api/v3/aschat/config`、`/api/v3/aschat/inspirations`、`/api/v3/aschat/sessions/.../records`、`/api/v4/aschat/sessions/.../events`、`/api/v4/aschat/sessions/.../state`。
5. **`listing-de.ke.com`（商铺写字楼 / 商业房源管理，`authenticated_verified`）**
   - **核心模块**：`租赁房源`、`买卖房源`、`珍珠房源`、`录入房源`、`我的房源`、`我的举报`、`商办经纪人名单`、`实勘图` 等房源列表与待办核验模块。
   - **认证与后端 API 域**：经 `login.ke.com` CAS 签发 `crep-listing-token`、`de_uc_id`、`de_city_id`、`de_role_id`；后端接口全部在 `listing-de.ke.com`（`/api/user`、`/api/pc/city/config/info`、`/api/pc/permission/role/list`、`/api/pc/truehouse/todo`、`/api/pc/kemis/pc/audit/todo`、`/api/pc/kemis/pc/house/list`、`/api/pc/kemis/dict/*`、`/api/pc/kemis/reportv2/*` 等 13 个业务接口，统一返回 `biz_code=100000`）。

### 3.2 新确认公网可用的业务/AI/协同系统（6 站 + `app-moma.ke.com` 架构定位）
1. **`luoshu.ke.com`（洛书动态表单引擎，`authenticated_verified`）**
   - **核心功能**：承载客服中心与员工/HR 门户的动态表单流程（如首看保护 `dynamicFormRender`、印章管理、访客预约、干部管理系统等）。
   - **认证与后端 API 域**：经 `login.ke.com/login` -> `/web/login/cas` 签发 `SESSION`、`lianjia_token_sinan`、`lianjia_token_sinan_prod`；后端接口在 `luoshu.ke.com`（`/web/loginUser/info`、`POST /web/fe/v1/form-plat/dynamicFormWebServiceImpl/initDynamicForm`，`biz_code=1`）。
2. **`m-jury.ke.com`（B 端纠纷调解平台，`authenticated_verified`）**
   - **核心功能**：经纪人/B 端纠纷调解案件列表（`/new/mediation/agentList`，含 `全部` / `未完结` / `已完结` 状态 Tab）。
   - **认证与后端 API 域**：经 `login.ke.com/login` 签发 `lianjia_token`、`ke_dk_u_id`、`ke_dk_u_name`、`ke_do_dk_u_id`；后端接口在 `m-jury.ke.com`（`/api/jury/getUserInfo`、`/api/mediation/agentMediationList`、`/api/mediation/mediationOpenCity`，均 `biz_code=0`）。
3. **`xiaobei.ke.com`（小贝知道 / 知识库与制度问答，`authenticated_verified`）**
   - **核心功能**：全站客服与员工制度知识库检索平台（覆盖作业规则、M 作战小组、IM/客源、财务制度指引等 FAQ 树与搜索）。
   - **认证与后端 API 域**：经 `login.ke.com/login` -> `/web/` 签发 `SESSION`；后端接口在 `xiaobei.ke.com`（`/web/loginUser/checkUserLogin`、`/web/loginUser/info`、`/web/fe/knowledge/knowledgeServiceImpl/dataInitForEndpoint`、`/web/fe/knowledge/relationalServiceImpl/getNewFistRelationalTreeListForC`、`/web/fe/knowledge/knowledgeOpenApiServiceImpl/search`、`/web/fe/knowledge/agentConfigApiServiceImpl/getDetailsWithSub`、`/web/fe/helpdesk/saasSkinServiceImpl/changeSkin`）。
4. **`chatbi.ke.com`（Chat BI 对话式数据智能助手，`authenticated_page`）**
   - **核心功能**：注：AI 门户卡片链接原指向 `/no-permission`，实测直接访问根路径 `https://chatbi.ke.com/` **公网可正常登录**！采用 Shadow DOM（`shadow_dom_detected=true`），包含 `历史`、`技能`、`知识` 等侧栏模块。
   - **认证与后端 API 域**：经 `login.ke.com/login` CAS 签发 `bigdata_bicopilot_token`、`odin_token`、`session_id`；后端接口在 `chatbi.ke.com`（`/ajax/common/getPermissionMenu`、`/ajax/chatbi/getBizType`、`/ajax/chatbi/getChatModelsList`、`/ajax/chatbi/getSessionPageList`、`/ajax/chatbi/getUserLabel` 均 `biz_code=0`，`/ajax/bicopilot/getUcLabel` 返回 `200`）。
5. **`is-prod.ke.com`（闹海 / 小伯乐员工智能助理，`authenticated_page`）**
   - **核心功能**：`/pages/chat` 自动跳转 `/pages/naohai`（`闹海` 智能体对话与技能平台）。
   - **认证与后端 API 域**：经 `login.ke.com/login` -> `/callback` 签发 `intelligentService_sid`、`intelligentservice_token`、`JSESSIONID`、`rememberMe`；调用 `GET is-prod.ke.com/admin/skill/queryMainPage`（`biz_code=0`）。
6. **`aiglasses.ke.com`（智眸 AI 眼镜平台，`authenticated_page`）**
   - **核心功能**：公开/免 CAS 门户展示与业务入口，导航包含 `首页`、`视频列表`、`文件上传`，涵盖能力矩阵（图像语音混合采集、提词神器等）及房产场景（楼盘采集、房屋核验、看房沟通）。
   - **认证与后端 API 域**：直出 HTTP 200 页面，未触发 `login.ke.com` 跳转及后台 XHR。
7. **`app-moma.ke.com`（Moma 轻应用部署托管域定位确认，`authenticated_page`）**
   - 直接访问裸根路径 `https://app-moma.ke.com/` 会 302/路由跳转到 `https://moma.ke.com/deploy_404`（`MOMA-页面未找到`），调用 `prd-assistant-backend.ke.com` 的 `/api/public/homepage/bootstrap`（`biz_code=0`）与 `/api/user/check-auth`（`biz_code=401`）。证实 `app-moma.ke.com` 为 Moma 已发布轻应用的运行时子域（需携带具体应用路径/ID），而非独立站点首页。

### 3.3 公网安全网关拦截站点（4 站 `denied_403`）
`ai-sec.ke.com`（贝壳安全AI助手）、`bella.ke.com`（Bella）、`jianli.ke.com`（聘多多系统）、`opengpt.ke.com`（ChatGPT 4）在公网直连时均跳转至 `https://hip.ke.com/forbidden?id=[REDACTED]&reason=...`（页面标题 `Forbidden`，提示「您的请求未能通过我们的安全检查」）。建议在 Phase 2B 内网隧道环境下抽检确认其是否仅对公网源 IP 触发 HIP 策略阻断。

---

## 4. 转入 Phase 2B 内网隧道复测清单（共 6 站）

以下 6 站已自动落地至 [phase2a_handoff_to_2b.json](./phase2a_handoff_to_2b.json)，将在 Phase 2B 复用 `scripts/phase2_site_profiler.py --use-tunnel --resume` 接力测绘：

| # | 主机 (`host`) | 系统名称 | 原批次分类 | 入口 URL | 直连失败原因 (`errors`) |
| ---: | --- | --- | --- | --- | --- |
| 1 | `chat-excel.ke.com` | CHAT-EXCEL | `ai_portal_candidate` | `https://chat-excel.ke.com/` | `net::ERR_CONNECTION_CLOSED` |
| 2 | `codelink.ke.com` | CodeLink | `ai_portal_candidate` | `https://codelink.ke.com/` | `net::ERR_CONNECTION_CLOSED` |
| 3 | `ketest-pro.ke.com` | Ketest Copilot | `ai_portal_candidate` | `https://ketest-pro.ke.com/#/keTest-copilot` | `net::ERR_CONNECTION_CLOSED` |
| 4 | `speechhub.ke.com` | 贝壳语音 | `ai_portal_candidate` | `http://speechhub.ke.com/` | `net::ERR_EMPTY_RESPONSE` |
| 5 | `app-fly.ke.com` | 员工文化应用 | `reappeared_page_only_unverified` | `https://app-fly.ke.com/employee-relations-hub` | `net::ERR_CONNECTION_CLOSED` |
| 6 | `jixiao.ke.com` | 绩效与试用期系统 | `reappeared_page_only_unverified` | `https://jixiao.ke.com/p/home` | `net::ERR_CONNECTION_CLOSED` |

---

## 5. 脱敏自检与进程隔离确认

- **脱敏规则修复与全量扫描**：[redaction_check.py](./scripts/redaction_check.py) 已更新 `cas_tgc_ticket` 正则为 `\b(?:TGT|TGC?)-[A-Za-z0-9._-]{8,}\b` 并跳过所有 `*_redaction_check.json` 自身文件。
- **离线自检结果**：对全工作区（含 Phase 1 与 Phase 2A 全部 `.json` / `.jsonl` / `.md` 交付物）运行扫描并生成 [phase2a_redaction_check.json](./phase2a_redaction_check.json)，结果：`passed == true`、`findings == []`。
- **仓库与进程状态**：`BeikeMicroservice` 零修改；Batch 2A 全程未启用 SSH SOCKS5 隧道，脚本退出后已关闭全部 Playwright 浏览器实例与临时上下文，无残留孤儿浏览器或隧道进程。
