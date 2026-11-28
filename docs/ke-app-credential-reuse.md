# 贝壳周边应用：CRM 凭证复用与只读接口研究

验证日期：2026-11-20。本文是供后续 Agent 使用的研究入口，不代表已发布的 Connector 功能或厂商正式接口契约。测试结果为当日、当前账号及当前版本下的小样本快照。

## 先读结论

| 目标 | 已验证的凭证路径 | 实测结果 | 详情 |
| --- | --- | --- | --- |
| [Moma 首页](https://moma.ke.com/nchat) | CRM 保存的 `TGC` → 官方 CAS → Moma 自己的会话 | 进入首页；用户信息接口 200；主体与 CRM 一致 | 本文“认证复用” |
| [豌豆 PC](https://wandou.ke.com/chat) | CRM 的 `puzu_lease_token` → 目标站 `localStorage.token` → Bearer 请求 | 进入首页、刷新正常；用户信息、客源、在售房源分页与单条回查通过 | [豌豆实测接口](../services/crm_connector/docs/wandou-api-probe-2026-11-20.md) |
| [Moma 文案 App](https://app-moma.ke.com/60758c5d-5f65-47aa-8238-645d8caaffb1/#/) | 登录时用 TGC 经 CAS 换取该 App 的 Supabase 会话；部分业务接口无需个人登录会话，但仍有应用级签名 | 普租单套查询、新房、文案、图片等已测业务接口成功；网页入口要求登录 | [文案 App 实测记录](../services/crm_connector/docs/moma-app-access-probe-2026-11-20.md) |

**最重要的区别：登录身份、应用会话、网络通道、业务数据覆盖是四个不同的验收对象。**页面返回 200 或出现首页，不能代替用户身份和真实业务接口验证。

## 认证复用

### Moma 主站

1. 通过现有 CRM 凭证管理边界取得当前有效的刷新材料，仅在内存中使用。
2. 在隔离浏览器中将现有 TGC 相关 Cookie 放到 `login.ke.com` 的正常作用域。
3. 打开 `https://moma.ke.com/nchat`，让网站自行发起官方 CAS 跳转。不要复用旧的单次 service ticket，不要改写站点登录判断。
4. Moma 完成回调后种下自己的会话 Cookie，实测包含 `prd-assistant-token-prod`。
5. 对照 `https://prd-assistant-backend.ke.com/api/user/info` 返回的当前用户主体与 CRM 绑定主体是否一致，同时检查业务响应及页面状态。

本轮结果：最终页面为 `/nchat`，标题为 MOMA 首页；用户信息接口返回 200，主体一致。自动加载的相关业务配置接口也返回 200。没有测试发送 Moma 对话或所有功能权限。

### 豌豆

豌豆当前 PC 前端使用 `localStorage.token` 中的令牌，自动发送：

```http
Authorization: Bearer <当前有效的 puzu_lease_token>
X-Wandou-Client-Id: wandou-pc
```

已验证的初始化方式：只在 `https://wandou.ke.com` 这个 origin 的隔离上下文中设置真实、现有的 `token`，然后访问 `/chat`。页面会通过 `/api/v1/users/me` 获取当前用户资料；不需要伪造用户资料、角色或管理员状态。

| 材料/操作 | 豌豆实测结果 |
| --- | --- |
| 仅在统一登录域保留 TGC，直接访问豌豆 | 仍进入手机号验证码登录页，没有自动建立豌豆会话 |
| 先登录 Moma，再在同一浏览器上下文访问豌豆 | 仍进入豌豆登录页；共享 Moma Cookie 不足以登录豌豆 |
| 将 `security_ticket` 作为 Bearer | 401，`U001` |
| 将 `saas_token` 作为 Bearer | 401，`U001` |
| 将 `puzu_lease_token` 作为 Bearer | 配置、身份和多项业务查询通过；进入首页并通过刷新复测 |

豌豆 `/api/v1/users/me` 返回的 UCID 与 CRM 主体一致，当前账号城市为成都。有效令牌被服务端接受后才判定登录成功，不以本地存储中存在字符串为依据。

令牌有效期、长期自动续期和并发尚未实测。需要续期时沿用项目既有 CRM 正常刷新流程，再为目标应用更新当前有效令牌；不要将 CRM 的本地过期估算直接当成豌豆令牌的真实 TTL。

### 文案 App 与 CRM 的正反向关系

- TGC 可以经官方 CAS 为文案 App 换取独立的 Supabase access/refresh token；实测该 App 的访问令牌响应 `expires_in=3600`。
- 只有 App 的 access/refresh token 时，不能直接当作 CRM 的业务 Cookie。实际仅带 App Bearer 的测试没有建立 CRM 认证。
- 如果保留了登录时的统一登录 TGC，可以再经 CRM 自己的正常 CAS 跳转换取新的 CRM 业务 Cookie。曾在没有预先放入 CRM 业务 Cookie 的会话中验证成功，身份接口业务码为 `100000`，主体一致。
- Moma 主站、文案 App、豌豆各自的会话材料与登录入口不同，不应把几个名称相近的网站当成同一个认证系统。

## 豌豆业务数据：已经证明什么

| 验证 | 当日结果 |
| --- | --- |
| 不带令牌查询客源/房源 | 均为 401、`U001` |
| 带当前 CRM 普租令牌查询客源 | 200、`success=true`、`code="0"`；总数 2，取回 1 条 |
| 在售房源关键字“华润”搜索 | 200；总数 4477，每页取 1 条 |
| 第 1 页、第 2 页 | 返回不同房源，分页元数据正确 |
| 使用列表返回编号单条回查 | 返回同一套房源的结构化字段 |
| 本人维护房源查询 | 连续两次 HTTP 502、HTML 响应；未通过 |

以上是直接业务接口结果，不依赖聊天模型对数据的描述。数量是动态业务数据，不是今后复测的固定期望值。

**本次豌豆房源样本明确为 `businessType=SALE`、`businessResourceType=0`、`housedelType=1`。**不能因为使用的是“普租令牌”，就将返回结果当成租赁房源。Wandou 类型编号也不能直接套用 CRM 归档的枚举。

客源接口实测只返回基础字段 `customerId/name/avatar`；房源返回编号、小区、户型、朝向、楼层、面积、价格/单价及图片字段。完整跟进、租赁详情与生产归档所需字段尚未验证。

## 网络通道与归档边界

- 上述 Moma/豌豆/文案 App 的浏览器及公开域名接口测试在用户本机完成，测试时未检测到云枢进程。CRM 凭证来源为既有云端服务。
- 这只证明被测站点和业务接口的本机可达性，不代表原始 CRM 域名也不再依赖云枢。
- 同一轮无云枢本机对原始 `lease-pz.link.lianjia.com`、`house.link.lianjia.com` 的访问发生连接重置；不能据单一网络环境外推所有部署环境。
- 已归档 SQLite 的本地读取本来就不访问 CRM，不需要 App 凭证或云枢。
- 豌豆已证明在售房源关键字搜索、分页、回查；文案 App 已证明单套房源查询。尚未证明完整列表发现、上下架同步、租赁与托管覆盖、全部详情字段和长期限流/稳定性。

**维持现有生产 CRM 与云枢归档通道。**如果以后开发新 Adapter，应逐项验证覆盖，再决定是否替换某一采集环节。

## 后续 Agent 的最短阅读与复测顺序

1. 读本文与对应目标的实测接口记录。
2. 通过项目现有 `CredentialStore` / `SessionProvider` 边界取得当前有效材料；服务器定位、SSH 别名见[服务器连接说明](deployment-servers.md)。不要从本报告寻找实际令牌。
3. 使用隔离上下文、目标 origin 白名单；凭证不放 URL、不输出到日志、不存入仓库、SQLite、报告或普通 token 文件。证据只保存状态、字段名、条数及主体一致性。
4. 先验证当前用户与 CRM 主体一致，再做一个权限范围内的只读业务查询；检查 HTTP 状态、业务成功字段及数据内容，而不是只检查页面。
5. 对已失败的 `negotiation/page` 保留失败标记；502 不等于认证失败，不要因此重复登录或发送验证码。
6. 不把历史总数、前端构建文件名和私有接口路径视作永久契约。重新验证后再扩大范围。

现有实现位置：

- `services/crm_connector/app/infrastructure/kecom_qr_bootstrap.py`：官方 CAS 引导与 TGC 刷新。
- `services/crm_connector/app/infrastructure/kecom_session_provider.py`：受控 origin/route 与认证请求边界。
- `services/crm_connector/app/infrastructure/windows_dpapi_credential_store.py`：现有凭证持久化边界。
- [现有认证协议分析](crm-auth-flow-analysis.md)。

优先待验证：豌豆租赁/托管类型与价格单位、完整详情/跟进字段、全量发现及上下架同步、令牌续期，以及维护房源 502 的原因。

本次只增加研究文档和索引，没有新增生产 HTTP/MCP 工具、放宽路由白名单、部署应用或重启服务。后续产品化时仍应沿用项目认证和受控请求分层；Hermes 不在维护范围。

## 房源查询认证边界专项审计（2026-11-20）

[综合安全审查报告（最终合并版）](kecom-security-audit-2026-11-20/kecom-security-audit-report-2026-11-20.md) 与 `docs/kecom-security-audit-2026-11-20/evidence/`（一期 21 份 + 二期 17 份 JSON，共 38 份脱敏证据）已整合全部一期、离线深挖及二期视点 B/视点 A 核验结论。

关键结论摘要：文案 App 公开 anon JWT 与普通用户均被实证可直读 Supabase `admins.password_hash`、`users` PII 并写入 `prompts`（已 201→204 清理），解密接口匿名/登录态均返回 `code=0` 信封，HMAC Nonce 未去重且缺少并发熔断；豌豆除普租令牌外亦接受托管令牌，且文件上传无扩展名/路径穿越校验；CAS `/logout` 服务端吊销 `TGC` 与豌豆旧 Bearer 失效均已证有效，但 Moma `prd-assistant-token-prod` 登出后重放仍 200（会话失效不一致）。完整风险矩阵、有效防线与待复测阻塞项见唯一主报告。


## 其他业务入口复核（2026-11-26）

新增[当前账号的凭证复用入口核实清单](crm-sso-entry-review-2026-11-26/README.md)与同目录脱敏证据。本轮发现45个候选主机，30个原始入口通过认证；按最终主机合并别名后为28个，其中8个核对身份接口、20个完成认证页面门禁验证。排除买卖、普通租赁、托管三个核心工作台后为25个其他认证入口。

本机直接验证通过的5个入口为Moma、豌豆、AI门户、客服中心和商铺写字楼新版房源列表。通知中心kemcc已在云端网络实际取得与CRM一致的IM身份，会话列表业务码0、可见会话0条；未验证消息发送权限。小区入口hestia虽然经过SSO，前端最终跳到Forbidden页，不能记为业务可用。完整状态与范围边界以新增清单为准，不将认证页面通过等同于全部业务权限。

## 全生态旁路测绘与目标企业系统架构全景（2026-11-27）

新增独立研究目录 [目标企业全生态旁路测绘与系统架构全景](ke-ecosystem-architecture-2026-11-27/README.md)（面向目标企业全生态信息系统，非 `BeikeMicroservice` 自身架构）。在 2026-11-26 的 28 入口初筛基础上，深度挖掘 7 大聚合枢纽并完成 **156 个独立主机浏览器深扫 + 17 个轻量快筛淘汰 = 173 站闭环**，实证纠偏原 28 入口（余 24 个独立可用业务实体，覆盖率 27.0%）并**净新增 65 个独立可用业务系统**（全生态合计 91 个可用入口 / 89 个独立实体，其中公网直连可用 11 站、内网隧道独有可用 80 站）。

- [遗漏核查、纠偏项与全量分层资产台账](ke-ecosystem-architecture-2026-11-27/01-omissions-and-inventory.md)
- [7 大业务域旁路系统功能与 API 手册](ke-ecosystem-architecture-2026-11-27/02-bypass-capability-catalog.md)
- [目标企业整体系统架构全景报告（含 4 张 Mermaid 图）](ke-ecosystem-architecture-2026-11-27/03-enterprise-system-architecture.md)
- 机器可读唯一脱敏事实源：`docs/ke-ecosystem-architecture-2026-11-27/final_ecosystem_dataset.json`（全目录离线脱敏自检 `passed: true`）。
