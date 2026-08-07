# 个人 CRM MCP Connector：架构与实施任务

## 1. 目标与边界

本方案为每位员工提供一套独立的 CRM MCP Connector，用于将其**本人已有权限范围内**的 CRM 查询能力提供给 Codex、OpenCode 等 Agent。

第一阶段目标：

- 日常查询不依赖浏览器自动化；
- Connector 运行在员工专属、已接入云枢的 Windows VM；
- 对外暴露语义化 MCP 工具，不暴露通用 HTTP 代理；
- 查询请求通过 CRM 的 HTTP 接口执行；
- 认证失效、云枢不可达或上游接口变化时，返回结构化错误并停止业务调用；
- 首期只读，优先完成“房源 → 租赁”查询。

非目标：

- 不建设共享的员工登录态中心；
- 不将凭据、会话材料、二维码或认证头返回给 Agent；
- 不在代码、日志、镜像、测试素材或配置样例中保存真实凭据；
- 不提供任意 URL、任意请求头、任意请求体的 HTTP 转发能力；
- 不在第一阶段支持写入、签约、改价或客户联系方式变更。

## 2. 已知约束

1. CRM 网页访问依赖云枢网络。关闭云枢后，CRM 网页无法正常工作；因此 Connector 的 CRM 请求默认也必须在云枢已连接的 Windows VM 内发起。
2. 当前已观察到 Link/CAS 扫码登录与房源查询页面；未验证 CRM 是否提供正式的 OAuth API 或稳定的服务端 API 契约。
3. 每位员工只服务本人：一台 VM、一套云枢终端身份、一个 CRM 主体、一个 Connector 实例。
4. 稳定性并非第一阶段首要目标，但接口变化、认证失效和网络中断必须可检测、可诊断、可恢复。

### 2.1 可见导航与模块边界

当前已登录页面显示的 CRM 顶层导航为：**首页、房源、客源、线索、签约、签后、应用**。其中“房源”下有：**买卖、租赁、新房、商铺写字楼、小区**。

本服务按下列边界演进：

| 业务域 | 子模块 | 当前状态 |
| --- | --- | --- |
| 房源 | 租赁 | 第一阶段实现，只读 |
| 房源 | 买卖、新房、商铺写字楼、小区 | 预留 |
| 客源、线索、签约、签后、应用 | 顶层业务域 | 预留 |

当前页面中“买卖”提供了全部房源、地铁/学校/地图找房、成交房源、钥匙管理、我的房源和录入房源等能力；它们仅用于识别未来模块边界，不作为第一期交付范围。租赁的实际接口字段在取得其受控接口契约后再固化，避免把买卖字段误用于租赁。

## 3. 总体架构

```mermaid
flowchart LR
    A["员工的 Codex / OpenCode / 其他 Agent"] -->|MCP| B["MCP 入口"]
    B --> C["调用者与员工绑定校验"]

    subgraph VM["员工专属 Windows VM"]
        C --> D["CRM MCP Server"]
        D --> E["领域 Adapter\n租赁房源（第一期）"]
        E --> F["SessionProvider\nauthorizedFetch"]
        F --> G["CredentialStore\n受保护凭据存储"]
        F --> H["CRM HTTP Adapter\n受控路由"]
        H --> I["云枢客户端"]

        J["授权中心\n本地 UI / API"] --> K["CredentialBootstrapProvider\n会话材料引导"]
        K --> L["CRM Principal 验证"]
        L --> G
        D --> M["脱敏审计与健康状态"]
    end

    I --> N["CRM 内部 HTTP 接口"]
    J["管理面\n版本、策略、健康状态"] -.不保存用户会话.-> D
```

### 3.1 部署模式

| 场景 | MCP 传输 | 调用者保护 |
| --- | --- | --- |
| Agent 与 Connector 都在同一 VM | `stdio` | Windows 用户边界与进程权限 |
| Agent 在员工电脑，Connector 在员工 VM | Streamable HTTP | 企业私网/SASE + mTLS 或 OIDC/OAuth |

远程 MCP 的调用者主体必须与该 VM 的绑定员工一致。Connector 启动后也应调用 `crm_whoami` 验证 CRM 返回的主体；三者不一致时拒绝业务工具调用。

### 3.2 进程职责

| 进程 | 职责 | 不负责 |
| --- | --- | --- |
| `crm-authd` / 授权中心 | 发起授权、协调引导器、CRM 主体验证、刷新、失效和本地交互 | 房源/客户业务映射、MCP 协议 |
| `CredentialBootstrapProvider` | 从部署侧的认证来源取得或更新当前员工可用的会话材料 | 业务工具、Agent 调用处理 |
| `CredentialStore` | 按员工 VM 持久化当前有效材料、版本和失效状态 | 业务查询、MCP 协议 |
| `crm-connectord` | 领域请求构建、输入与输出校验、CRM 调用编排、脱敏审计 | 向 Agent 暴露凭据 |
| `crm-mcp` | MCP tool 定义、Agent 调用者认证、错误映射 | 保存登录会话或直接处理认证材料 |

`CredentialBootstrapProvider`、`crm-authd` 与 `CredentialStore` 构成认证材料的唯一边界。其他进程只可请求“执行一个已认证的受控请求”，不得获得认证材料本身。

## 4. 核心接口

### 4.1 `CredentialBootstrapProvider`

`CredentialBootstrapProvider` 是部署实现的认证引导扩展点。它的结果必须是当前员工 Connector 可用的认证材料，而非对业务层开放的认证数据。

```ts
interface CredentialBootstrapProvider {
  bootstrap(): Promise<BootstrapResult>;
  refresh(current: ActiveCredential): Promise<BootstrapResult | null>;
  validate(credentialMaterial: unknown): Promise<PrincipalContext>;
  revoke(current: ActiveCredential): Promise<void>;
}
```

授权中心的固定编排：

```text
开始授权 / 刷新授权
  → CredentialBootstrapProvider 获取材料
  → 调用 CRM identity.me 验证主体
  → 校验主体 = CC_BOUND_EMPLOYEE_PRINCIPAL
  → CredentialStore 原子保存新版本
  → 旧版本失效
  → AuthCenter 状态切换为 ready
```

验证失败、主体不一致或引导器未返回有效材料时，状态必须保持 `auth_required` 或 `failed`，不能进入业务查询。

### 4.2 `SessionProvider`

```ts
type AuthStatus =
  | "ready"
  | "expiring"
  | "auth_required"
  | "network_required"
  | "degraded";

type AuthorizedRequest = {
  method: "GET" | "POST" | "PUT";
  route: "identity.me" | "rental_listing.search" | "rental_listing.get_detail";
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  requestId: string;
};

interface SessionProvider {
  status(): Promise<AuthStatus>;
  authorizedFetch(request: AuthorizedRequest): Promise<UpstreamResponse>;
  beginReauthentication(): Promise<{ sessionId: string; status: AuthStatus }>;
}
```

实现要求：

- `authorizedFetch` 只接受路由别名和结构化参数，不接受 URL 或任意 Header；
- 认证材料不得从接口返回；
- 认证失效时抛出可识别的 `CRM_AUTH_REQUIRED`；
- 云枢未连接或 CRM 网络不可达时返回 `CRM_NETWORK_REQUIRED`；
- 所有调用带 `requestId`，用于关联日志；
- 并发刷新必须加锁，避免多个 Agent 同时触发认证更新。
- `authorizedFetch` 从 `CredentialStore` 读取当前有效材料，通过 CRM HTTP Adapter 调用受控路由；不自行决定认证来源。

### 4.3 `CredentialStore`

`CredentialStore` 只被 `crm-authd` 使用，用于保存当前员工 Connector 的有效认证材料及最小会话元数据。

```ts
interface CredentialStore {
  save(input: {
    sessionId: string;
    employeePrincipal: string;
    credentialMaterial: unknown;
    expiresAt?: Date;
    credentialVersion: number;
  }): Promise<void>;
  loadActive(): Promise<ActiveCredential | null>;
  invalidate(sessionId: string, reason: "expired" | "upstream_rejected" | "replaced"): Promise<void>;
  clearExpired(now: Date): Promise<number>;
}
```

实现规则：

- 认证成功后，先验证 CRM 主体，再原子保存新认证材料并使旧会话失效；
- `SessionProvider.authorizedFetch()` 只能从 `CredentialStore` 获取当前有效材料并注入受控请求；
- 上游明确返回认证失效时，立即调用 `invalidate()`，状态切换为 `auth_required`；
- MCP、业务 Adapter、REST 响应、授权中心页面和日志只能看到状态、会话 ID、到期时间等非敏感元数据；
- 认证材料不进入 SQLite、普通文件、`.env`、镜像、快照、测试夹具或集中管理面；
- Windows VM 使用当前 Connector 运行账户可访问的受保护凭据存储或 DPAPI 加密存储。

### 4.4 CRM 路由注册表

每个业务能力都使用受控路由定义，不提供通用请求代理。

```ts
interface CrmRoute<Input, Output> {
  name: "identity.me" | "rental_listing.search" | "rental_listing.get_detail";
  buildRequest(input: Input, principal: PrincipalContext): AuthorizedRequest;
  parseResponse(response: UpstreamResponse): Output;
}
```

路由注册表保存：请求方法、路径别名、允许参数、请求构建规则、响应 Schema、错误码映射。真实上游地址仅由部署环境配置注入，不能写入仓库。

## 5. MCP 工具设计

### 5.1 第一阶段：只读工具

| 工具 | 用途 | 风险等级 |
| --- | --- | --- |
| `crm_connection_status` | 返回云枢、认证和上游连通状态 | 低 |
| `crm_whoami` | 校验当前 CRM 主体与本机员工绑定 | 低 |
| `rental_listing_search` | 查询租赁房源 | 中 |
| `rental_listing_get_detail` | 查询单套租赁房源详情 | 中 |

`rental_listing_search` 的输入使用业务语言。首期只固化租赁与买卖共通的检索字段；租金、出租方式和租期等租赁专属字段需在确认租赁接口契约后追加：

```json
{
  "communityKeyword": "可选",
  "listingId": "可选",
  "maintainer": "可选",
  "scope": "my_maintained",
  "districts": ["示例区域"],
  "monthlyRentYuan": { "min": 2000, "max": 5000 },
  "areaSqm": { "min": 70, "max": 110 },
  "rooms": [2, 3],
  "orientations": ["南", "南北"],
  "tags": ["地铁房"],
  "page": 1,
  "pageSize": 20
}
```

约束：

- `pageSize` 设置上限，例如 50；
- 每个工具设置速率上限；
- 返回值只含完成当前任务所需字段；
- CRM 的真实权限永远优先于 Connector 自己的筛选逻辑；
- 不允许调用方传入或覆盖员工 ID、组织 ID、认证头、Cookie 或上游地址。

### 5.2 后续工具

在租赁只读工具稳定后，按以下顺序扩展：

1. 买卖、新房、商铺写字楼、小区查询；
2. 客户只读查询，默认最小化返回敏感字段；
3. 跟进草稿创建；
4. 明确的写入工具。

写入工具必须增加：人工确认、幂等键、参数预检、变更前后摘要、审计记录与限流。

## 6. 认证与用户交互

### 6.1 认证状态机

```mermaid
stateDiagram-v2
    [*] --> Ready
    Ready --> Expiring: 接近官方有效期
    Expiring --> Ready: 官方刷新成功
    Expiring --> AuthRequired: 刷新失败或不支持刷新
    Ready --> AuthRequired: 上游返回认证失效
    Ready --> NetworkRequired: 云枢或 CRM 网络不可达
    NetworkRequired --> Ready: 连通性恢复
    AuthRequired --> Ready: 员工完成授权
```

### 6.2 授权中心

扫码或其他人工授权应展示在 VM 的独立“CRM Connector 授权中心”中，而不是嵌入 MCP 工具响应。

授权中心应展示：

- 当前绑定员工和 VM 标识；
- 云枢连接状态；
- 当前状态：等待授权、已扫描、等待手机确认、成功、已过期；
- 二维码有效期与刷新操作；
- 明确的“授权成功后业务工具将自动恢复”说明。

MCP 只返回如下类型的错误，不返回二维码、会话材料或认证 URL：

```json
{
  "code": "CRM_AUTH_REQUIRED",
  "message": "CRM 授权已失效，请在员工专属 Connector 授权中心完成授权。",
  "retryable": false
}
```

### 6.3 认证续期原则

- 优先使用 CRM/SSO 明确支持的刷新机制；
- 不通过模拟点击、伪造活跃或重复业务请求延长会话；
- 无法刷新时，通知员工完成官方授权；
- 登录未恢复前，业务工具失败并保持幂等。

## 7. 配置、存储与日志

### 7.1 配置

配置只保存非敏感信息，例如：

```text
CONNECTOR_INSTANCE_ID
BOUND_EMPLOYEE_PRINCIPAL
MCP_TRANSPORT
MCP_LISTEN_ADDRESS
CRM_ROUTE_PROFILE
LOG_LEVEL
RATE_LIMITS
```

真实凭据、认证材料、Cookie、Token、二维码内容、CRM 真实地址不能进入 `.env.example`、版本库、日志或测试夹具。

### 7.2 本地存储

建议使用本地 SQLite 保存最小运行状态：

- Connector 版本；
- 路由契约版本；
- 认证状态与失效时间（不含认证材料）；
- 任务与调用审计摘要；
- 限流计数与错误统计。

认证材料由 `CredentialStore` 保存到 Windows 受保护凭据存储，且仅 `crm-authd` 可访问。SQLite 仅保存会话 ID、认证状态、到期时间、路由契约版本和脱敏审计摘要。

### 7.3 审计日志

必记字段：

```text
timestamp, request_id, connector_instance_id, caller_subject,
tool_name, result_code, latency_ms, upstream_status
```

默认不记录房源完整地址、客户姓名、电话、认证相关字段或完整上游响应。诊断模式也必须使用字段白名单与脱敏规则。

## 8. 推荐项目结构

```text
services/crm_connector/
  README.md
  pyproject.toml
  app/
    api/
      mcp_server.py
      health.py
    application/
      search_rental_listings.py
      get_rental_listing_detail.py
      get_connection_status.py
    domain/
      models.py
      errors.py
      providers/
        session_provider.py
        credential_store.py
        crm_routes.py
    infrastructure/
      crm_http_adapter.py
      authd_client.py
      sqlite_audit_repository.py
      settings.py
    main.py
  tests/
    unit/
    integration/
```

该目录遵循仓库既有的 `api -> application -> domain <- infrastructure` 依赖方向。若实际采用 TypeScript，应保持同等的分层和接口边界。

## 9. 实施任务

复选框按对照 `services/crm_connector/` 代码现状勾选，并在每条末尾用 `（证据: path:line）` 标注关键证据；状态标签：`[x] 完成`、`[~] 部分`、`[ ] 未做`，运维/云枢项标注 `（需云枢）`。

### 阶段 0：前置验证

- [ ] 指定试点员工与专属 Windows VM。（证据: `services/crm_connector/.env.example:3` `CC_BOUND_EMPLOYEE_PRINCIPAL=` 留空；`docs/deployment-servers.md` 仅 store_media 条目）
- [ ] 安装并验证云枢；确认 VM 能访问 CRM 网页入口。（需云枢；证据: `docs/crm-auth-flow-analysis.md:231-239` §5.2 列出云枢待测项）
- [~] 定义 Connector 绑定员工标识与 VM 标识。（员工标识已落 `app/infrastructure/settings.py:10,31`；VM 标识字段缺失，`Settings` 无 `connector_instance_id`/`vm_id`）
- [~] 建立脱敏接口契约清单：身份校验、房源搜索、房源详情。（`docs/crm-auth-flow-analysis.md:139-209` §4 已固化 search + whoami 契约；`get_detail` 上游未抓，见同文 `:296`）
- [~] 记录每个接口的请求方法、路由别名、输入字段、分页方式、响应字段与错误码。（search/whoami 已记录；`get_detail` 待抓，错误码 `CRM_UPSTREAM_CHANGED`/`CRM_UPSTREAM_INVALID_INPUT` 仅文档列出未落 `errors.py`）
- [x] 确定 `SessionProvider` 的授权、刷新、失效和人工恢复边界。（证据: `app/infrastructure/kecom_session_provider.py:125-188,287-311`；接口 `app/domain/providers/session_provider.py:26-33`）
- [ ] 明确 CRM 数据使用范围、日志保留期和员工授权责任。（仓库无 crm_connector 专用的数据使用/日志保留/责任文档；`docs/security.md`、`docs/privacy.md` 为通用文档）

**验收标准**：有一份不含真实凭据的接口契约文档；试点 VM 在云枢开启时能访问 CRM，关闭时不可达或按策略被拒绝。

### 阶段 1：授权与连接基础设施

- [x] 创建 `crm_connector` 服务骨架，并登记服务目录与所有者。（证据: `services/catalog.yaml:21-29` 含 owner；`docs/service-registry.md:9` 同步登记；`services/crm_connector/README.md` + `pyproject.toml` 齐全）
- [~] 实现健康检查：进程存活、云枢连通、认证状态、上游探测。（`app/api/router.py:39-41` `/health` 仅进程存活；`:44-48` `/connection/status` 只反映认证状态，无云枢连通探测、无上游探测）
- [~] 实现配置加载、请求 ID、结构化日志与字段脱敏。（配置 `app/infrastructure/settings.py:27-61`；请求 ID 在 `kecom_session_provider.py:131,268` 生成与注入但 HTTP API 入口未注入；日志仅 `app/authd/cli.py:23-26` 基础 `basicConfig`，无 JSON 结构化、无字段脱敏中间件）
- [ ] 实现 SQLite 审计仓储与日志轮转策略。（`grep` 无 `sqlite*`/`*audit*` 文件；`app/infrastructure/` 下无 `sqlite_audit_repository.py`）
- [x] 实现 `CredentialBootstrapProvider` 的部署适配器；不使用 Mock 作为生产链路。（证据: `app/infrastructure/kecom_qr_bootstrap.py:97-160` 真实 `httpx.Client` 走 passport-web flow；`app/authd/cli.py:31-32` 装真实 provider；**2026-08-06 真实扫码验证 bootstrap 全链路通过**：`authenticate` 200 + TGC → CAS lease-pz hop `puzu_lease_token` → CAS shiro-cas hop `saas_token` → `accountRightInfo` code=100000）
- [x] 实现 `CredentialStore` 接口与 Windows 受保护存储适配器。（证据: `app/domain/providers/credential_store.py:26-43` 接口；`app/infrastructure/windows_dpapi_credential_store.py:24-141` DPAPI 实现覆盖全部 4 方法，`:214-232` 原子写，`:235-245` CryptProtectData）
- [x] 在认证成功后执行“CRM 主体验证 → 新凭据原子保存 → 旧会话失效”。（证据: `app/infrastructure/kecom_session_provider.py:157-188` `install_fresh_credential` 顺序 `validate:165 → save:184 → invalidate previous:186-187`）
- [x] 上游认证拒绝时执行 `CredentialStore.invalidate()`，并同步更新授权中心状态。（证据: `kecom_session_provider.py:143-147,287-311,368-379` `_deactivate` 调 `invalidate` 并清 `_active`；`app/authd/server.py:48-89` 暴露 `/status /poll /keepalive /notify`）
- [x] 实现 `SessionProvider.authorizedFetch()` 与 CRM HTTP Adapter 的受控路由调用链。（Provider 侧完整：`kecom_session_provider.py:125-149,247-285,440-450` `_ROUTE_TABLE`；CRM Adapter `KecomCrmClient` 已实现 `kecom_crm_client.py`；`app/main.py:36-52` `_build_providers` 已加 `upstream_profile != "unconfigured"` 装配分支，真实生产链路接入 `KecomSessionProvider` + `KecomCrmClient`；默认 profile 仍用 `Unconfigured*` 保 CI 安全；**2026-08-06 真实链路验证**：`search_rental_listings` 返回 10 条真实房源（例 `ICC凯旋门 月租3850元`），`run_keepalive` 返回 `ConnectionState.READY`，`_COMPAT_COOKIES` 含 `saas_token` 且自动注入每次 `authorizedFetch`）
- [x] 实现认证状态机、刷新互斥锁和 `CRM_AUTH_REQUIRED` 错误。（5 状态齐全 `app/domain/models.py:8-13`；刷新锁 `kecom_session_provider.py:89,294`；`NetworkRequiredError` `:285`；**2026-08-06 验证 refresh 全链路**：`bootstrap.refresh()` 用持久化 TGC 走 CAS 双跳 → 新 `puzu_lease_token`+`saas_token` 种入 → `BootstrapResult` 正常返回，**无额外扫码**；`_maybe_autorefresh:287-311` 并发锁已实现；缺：`network_required → ready` 恢复转换、`_derive_state_locked` 不直接返回 NETWORK_REQUIRED）
- [x] 为本地授权中心预留状态查询和通知接口。（证据: `app/authd/server.py:48-89` `build_app` 提供 status/poll/keepalive/notify 4 个端点；`app/authd/cli.py:60-75` `cmd_status` CLI）

**验收标准**：授权中心可使状态进入 `ready`；无有效授权时所有业务调用返回 `CRM_AUTH_REQUIRED`；云枢不可达时返回 `CRM_NETWORK_REQUIRED`；日志无认证材料。

对照代码现状（2026-08-06 完整链路验证）：授权中心已具备状态转换能力（`install_fresh_credential` 走完即 `ready`）；Bootstrap 真实链路已验证（扫码 → CAS 双跳 → 全套 cookie 种入），`refresh()` 用 TGC 无额外扫码也验证通过。无有效授权返回 `CRM_AUTH_REQUIRED` 由 `UnconfiguredSessionProvider` 兜底（`tests/integration/test_api.py:42-50` 已验）；`CRM_NETWORK_REQUIRED` 由 `kecom_session_provider._send_authorized:285` 抛出。 "日志无认证材料"目前无强制机制（无脱敏中间件/无 sanitize 测试），需补强。

### 阶段 2：租赁房源查询 POC

- [x] 实现 `identity.me` 路由和 `crm_whoami`。（路由已注册 `kecom_session_provider.py:441`；`ConnectorService.whoami` `app/application/service.py:47-51` 已实现并校验绑定；`KecomCrmClient.whoami` 已实现 `app/infrastructure/kecom_crm_client.py` 走 `identity.me` 路由并解析 `accountRightInfo` envelope；端到端覆盖见 `tests/integration/test_api.py::test_whoami_flows_through_full_app_pipeline`；**2026-08-06 真实链路验证**：`run_keepalive` → `accountRightInfo?typeList=2` 返回 `code:100000`，`ConnectionState.READY`）
- [x] 实现员工主体与 Connector 绑定校验。（证据: `app/application/service.py:70-73` `_verify_bound_principal`；`app/authd/cli.py:124-131` `_assert_principal_matches`）
- [x] 实现 `rental_listing.search` 请求构建、响应解析与分页。（`KecomCrmClient.search_rental_listings` 已实现 `kecom_crm_client.py`：请求构建 `_route_query` 映射 `community_keyword`/租金/面积/户型/朝向等到上游文档化 query 参数；响应解析 `_parse_listing`/`_parse_page` 把 `data.result[].delCode/resblockName/bedroomAmount` 等映射到 7 字段 `RentalListing`，分页由 `totalCount` 推导；端到端覆盖 `tests/integration/test_api.py::test_search_wanxiangcheng_flows_through_full_app_pipeline`；**2026-08-06 真实链路验证**：调用真实 `lease-pz.link.lianjia.com/api/houseList/search/pc/list` 返回 10 条真实房源（例 `id=106106022223 community=ICC凯旋门 rent=3850.0`），`has_more=True`）
- [~] 实现 `rental_listing.get_detail`。（`KecomCrmClient.get_rental_listing_detail` 已实现，临时复用 search 路由 + `delCode` 限定单条 `kecom_crm_client.py`；端到端覆盖 `tests/integration/test_api.py::test_get_detail_flows_through_full_app_pipeline`；**专用上游路由仍未抓，见 `docs/crm-auth-flow-analysis.md` §8.3，待云枢补抓后切换**）
- [~] 为输入、输出和上游错误建立 Schema 校验。（输入 `app/api/schemas.py:75-107` 含 `additionalProperties=False` + `page_size<=50`；输出 `:110-138`；上游错误 Schema 已补 `CRM_UPSTREAM_INVALID_INPUT` (400) 与 `CRM_UPSTREAM_CHANGED` (502) `app/domain/errors.py:24-34`；端到端 `test_upstream_invalid_input_surfaces_as_400_detail` 和 `test_upstream_changed_surfaces_as_502_detail` 已覆盖两个新错误码的 HTTP 状态与 detail code）
- [~] 添加租赁房源结果字段最小化与数据脱敏策略。（字段已最小化 `app/domain/models.py:53-61` 仅 7 字段；`KecomCrmClient._parse_listing` 仅提取 7 个字段，丢弃上游所有埋点/电话/维护人字段；脱敏中间件仍未实现，需补 §7.3 的字段白名单中间件）
- [ ] 在授权员工范围内抽样比对 Connector 查询结果与租赁页面结果。（需云枢，`docs/crm-auth-flow-analysis.md:302` 一次性 smokedog 比对未做）

**验收标准**：Connector 在不启动浏览器的情况下可完成授权范围内的租赁房源查询；结果分页、筛选和错误状态符合契约。

对照代码现状（2026-08-06 完整链路验证）：(1) `bootstrap()` 真实扫码 → `authenticate` 200 + TGC → CAS 双跳（lease-pz hop → `puzu_lease_token`；house.link shiro-cas hop → `saas_token`）→ `accountRightInfo` code=100000。(2) `search_rental_listings` 真实调用 `/api/houseList/search/pc/list` 返回 10 条真实房源（例 `ICC凯旋门 月租3850元`），has_more=True。(3) `run_keepalive` → `accountRightInfo?typeList=2` code=100000，`ConnectionState.READY`。(4) `refresh()` 用 TGC 走 CAS 双跳无额外扫码。**尚需云枢**：(a) `rental_listing.get_detail` 切换到专用上游路由（待 §8 补抓）；(b) 在云枢 VM 用真实扫码 cookie 跑一次 §8 smokedog，比对 Connector 与浏览器对同一搜索条件的字段一致性。

### 阶段 3：MCP 接入

- [x] 定义 MCP tools、描述、输入 Schema 与返回 Schema。（`app/mcp/tools.py` 4 个工具均带 `name/description/input_schema/output_schema`：`McpToolDefinition` 含 `output_schema` 字段，`as_dict()` 输出 `outputSchema`；MCP 2.0 单模型入参经 `mcp/schemas.py` 的 `extra="forbid"` 模型嵌套在 `{"input": ...}` 下）
- [x] 支持 `stdio` 模式。（`app/mcp/server.py` 的 `build_mcp_server` 用 mcp 2.0 `MCPServer` 注册 4 个工具，`main()` 按 `CC_MCP_TRANSPORT` 校验后以 `server.run(transport="stdio")` 运行；`pyproject.toml` 注册 `crm-mcp = "app.mcp.server:main"`；2026-08-07 用真实 DPAPI 凭据在 `kecom-prod` profile 下经 stdio 子进程验证：whoami/search/detail 均返回真实 CRM 数据）
- [ ] 如需远程调用，支持 Streamable HTTP 与调用者认证。（当前范围仅本地 stdio 接入；`mcp.server` 的 `server.run` 亦支持 `streamable-http`，远程部署前需补调用者认证中间件，`main.py` 目前只有 CORSMiddleware）
- [~] 实现调用者主体、VM 绑定员工、CRM 主体三方一致性校验。（实现 2/3：`ConnectorService._verify_bound_principal` `app/application/service.py` 校验 CRM 主体 = `bound_employee_principal`；调用者主体经 `_caller_subject()`（`getpass.getuser()`）作为 MCP 工具限流身份；VM 绑定校验（云枢）未实现）
- [x] 添加工具级限流、只读工具白名单和超时控制。（只读：全部工具 `ToolAnnotations(read_only_hint=True)` 标注 + `tools.py` `read_only=True` 默认；限流：`app/mcp/rate_limit.py` 滑动窗口 `RateLimiter`，`crm_connection_status` 不限额，其余 3 个按调用者主体受 `CC_MCP_RATE_LIMIT_PER_MIN` 限制，超限返回 `RATE_LIMITED`；超时：`request_timeout_seconds=15` 经 `kecom_session_provider.py` 用于 `httpx.Timeout`）
- [x] 编写 MCP 契约测试和 Agent 调用样例。（`tests/integration/test_mcp.py` 11 个：工具发现/只读标注/状态不限额/三条业务管线/上游错误映射/未知字段拒绝/认证拦截/限流/Schema 元数据/stdio 子进程入口；`tests/unit/test_rate_limit.py` 4 个；Agent 调用样例 `examples/mcp_client.py`（status/whoami/search/detail/demo 子命令），2026-08-07 以真实 DPAPI 凭据经 stdio 跑通全链路，仓库根 `.mcp.json` 已注册 Claude Code 项目级 MCP server）

**验收标准**：Agent 只能发现并调用允许的语义工具；无法提交任意 URL、认证头或跨员工参数。

对照代码现状：MCP stdio 传输已实现并通过真实凭据验证——`crm-mcp` 子进程（`app/mcp/server.py:main`）以 mcp 2.0 `MCPServer` 暴露 4 个只读工具，全部经 `SessionProvider.authorizedFetch()` 走受控路由，未知输入字段在 `schemas.py` 层即被 `extra="forbid"` 拒绝。三方一致性仍缺 VM 绑定校验一环（云枢主体解码）；Streamable HTTP 远程调用与调用者认证留待后续阶段。

### 阶段 4：可靠性与运维

- [ ] 为认证失效、云枢断开、上游 4xx/5xx、契约解析失败设置告警。
- [ ] 配置 Connector 自启动与异常重启。
- [ ] 提供“连接状态”和“需要人工授权”的本地通知。
- [ ] 增加路由契约版本号；上游解析失败时快速降级为 `CRM_UPSTREAM_CHANGED`。
- [ ] 实现只含脱敏摘要的诊断包。
- [ ] 制定 VM 补丁、备份、销毁与员工离职时的凭据清理流程。

**验收标准**：认证、网络和上游接口故障都有清晰、非误导性的错误状态与恢复路径。

### 阶段 5：受控扩展

- [ ] 增加买卖、新房、商铺写字楼、小区查询。
- [ ] 增加客户只读工具，先执行字段最小化与权限评审。
- [ ] 评审写入用例。
- [ ] 写入工具增加确认、幂等键、前后摘要和审计后再上线。
- [ ] 为每位新员工使用无凭据基础镜像创建独立 VM 和 Connector。

## 10. 测试计划

| 编号 | 场景 | 预期结果 |
| --- | --- | --- |
| T1 | 云枢已连接、认证有效、租赁房源查询 | 返回通过 Schema 校验的分页结果 |
| T2 | 云枢断开 | 返回 `CRM_NETWORK_REQUIRED` 或网络层受控错误 |
| T3 | 认证失效 | 返回 `CRM_AUTH_REQUIRED`，不触发业务重试 |
| T4 | 调用者员工与 VM 绑定员工不一致 | 返回拒绝错误，不请求 CRM |
| T5 | CRM 主体与 VM 绑定员工不一致 | Connector 进入降级状态并拒绝业务调用 |
| T6 | Agent 提交未知筛选字段 | 本地 Schema 校验失败，不请求 CRM |
| T7 | 上游接口响应字段变化 | 返回 `CRM_UPSTREAM_CHANGED`，记录脱敏诊断 |
| T8 | 并发调用触发认证刷新 | 只允许一个刷新操作，其他请求等待或返回明确状态 |
| T9 | 日志与审计检查 | 不包含认证材料、完整客户联系方式或完整上游响应 |
| T10 | 新认证成功 | 先校验 CRM 主体，再原子保存新凭据并使旧会话失效 |
| T11 | 上游认证失效 | 凭据被失效，后续业务调用返回 `CRM_AUTH_REQUIRED` |
| T12 | 凭据存储清理 | 过期材料被清理；SQLite 和日志只含非敏感元数据 |
| T13 | 凭据引导成功 | BootstrapProvider 返回材料后，CRM 主体通过绑定校验，状态进入 `ready` |
| T14 | 凭据引导失败 | Provider 无有效结果或主体不一致，状态保持 `auth_required`/`failed`，不请求租赁路由 |
| T15 | 已认证租赁请求 | SessionProvider 通过 CRM HTTP Adapter 调用受控租赁路由，返回经 Schema 校验的数据 |

**对照代码现状（`services/crm_connector/` 与 `tests/`）**

| T | 覆盖 | 关键证据 / 说明 |
| --- | --- | --- |
| T1 | ✅ **已验证** | 2026-08-06 真实链路：`KecomCrmClient.search_rental_listings` → `houseList` 返回 10 条真实房源（`ICC凯旋门 月租3850元`），`accountRightInfo` code=100000，`refresh()` 无额外扫码 |
| T2 | 代码已实现，无专门测试 | `kecom_session_provider.py:279-285` `httpx.HTTPError → NetworkRequiredError`；`grep test.*network` 无匹配 |
| T3 | 已覆盖 | `tests/unit/test_service.py:65-85` + `tests/integration/test_api.py:42-50,47,50` |
| T4 | 未覆盖 | 三方一致性校验未实现（见阶段 3 条目 22） |
| T5 | 未覆盖 | `_verify_bound_principal` 已实现但无单测触发不匹配路径 |
| T6 | Pydantic 默认覆盖，无显式测试 | `app/api/schemas.py:71,79,87` `additionalProperties=False` 等 |
| T7 | 未覆盖 | `errors.py` 无 `CRM_UPSTREAM_CHANGED` 枚举 |
| T8 | 代码已实现，无测试 | `kecom_session_provider.py:89,294` 双锁实现；无并发测试 |
| T9 | 未覆盖 | 无 sanitize/redact 测试或脱敏中间件 |
| T10 | ✅ **已验证** | `install_fresh_credential` `:157-188` 三步序列已实现；2026-08-06 真实扫码后 `result.json` 写入成功，`employee_principal=1000000031696069` |
| T11 | 未覆盖 | `_maybe_autorefresh`/`_deactivate` 已实现，无对应单测 |
| T12 | 部分覆盖 | `windows_dpapi_credential_store.py:127-141` `clear_expired` 已实现，无测试；SQLite 审计未实现 |
| T13 | ✅ **已验证** | 2026-08-06 真实扫码 bootstrap 通过 `validate` → `install_fresh_credential` → `ready`；`accountRightInfo` code=100000 |
| T14 | 部分覆盖 | `:124-184` 覆盖超时/过期；主体不一致路径（`_assert_principal_matches` SystemExit）未测 |
| T15 | ✅ **已验证** | 2026-08-06 真实链路：`KecomSessionProvider.authorizedFetch` → `KecomCrmClient.search_rental_listings` → 真实 `houseList` 返回 10 条房源 |

### §9 实施任务总结（按代码现状）

| 阶段 | 完成 `[x]` | 部分 `[~]` | 未做 `[ ]` | 阶段验收达成 |
| --- | --- | --- | --- | --- |
| 0 前置验证 | 1 | 3 | 3 | 文档层契约部分完成；VM/云枢/运维未做 |
| 1 授权基础设施 | 8 | 2 | 1 | **授权中心可进 `ready`，真实链路已验证**（bootstrap + refresh + houseList + accountRightInfo 全部 100000）；`CRM_NETWORK_REQUIRED` 从 HTTP 入口仅经 httpx HTTPError 触发，未独立测试 |
| 2 租赁查询 POC | 4 | 3 | 0 | **`KecomCrmClient` 已实现且真实验证**：`houseList` 返回 10 条真实房源；`get_detail` 临时复用 search 路由，专用上游路由待补抓 |
| 3 MCP 接入 | 0 | 3 | 3 | 无 stdio / 远程 MCP 传输、无三方一致性、无限流执行、无协议级测试 |
| 4 可靠性与运维 | 0 | 0 | 6 | 全部未做 |
| 5 受控扩展 | 0 | 0 | 5 | 全部未做（`modules.py` RESERVED 占位） |

**"扫码登录 → 查询房源"端到端链路已于 2026-08-06 完整验证打通**：

1. ✅ `app/infrastructure/kecom_crm_client.py`：`KecomCrmClient` 实现 `CrmClient` 协议（请求构建 + 响应解析 + 错误映射）。
2. ✅ `app/main.py`：`upstream_profile != "unconfigured"` 分支装配 `KecomSessionProvider` + `KecomCrmClient`。
3. ✅ `app/domain/errors.py`：`CRM_UPSTREAM_CHANGED`、`CRM_UPSTREAM_INVALID_INPUT` 枚举已补。
4. ✅ `app/infrastructure/kecom_qr_bootstrap.py`：`_establish_business_session` CAS 双跳种下 `puzu_lease_token` + `saas_token`。
5. ✅ **真实链路验证**：扫码 bootstrap → houseList 返回 10 条真实房源（`ICC凯旋门 3850元`）→ keepalive `READY` → TGC refresh 无额外扫码。
6. 待做：在云枢 VM 用真实账号跑一次 §8 smokedog，比对 Connector 与浏览器对同一搜索条件的字段一致性。

## 11. 上线门槛

以下条件全部满足后，才允许扩大到更多员工：

1. 试点员工在云枢连接环境下可用 API 查询完成真实日常任务；
2. 无浏览器常驻进程；
3. 认证失效、云枢断开、上游变化均有可操作的错误提示；
4. 认证材料不在仓库、日志、审计库或 MCP 响应中出现；
5. 调用者、VM、CRM 主体一致性校验通过；
6. 租赁只读查询的业务结果经人工抽样确认；
7. VM、云枢和 Connector 的所有权、更新和退出清理责任明确。

## 12. 待确认决策

- CRM 认证是否存在可由非浏览器客户端使用的正式刷新机制；
- 需要支持 `stdio`、远程 MCP，或两者同时支持；
- 员工 Agent 到 VM 的私网与调用者认证方案；
- 首期租赁房源查询允许的字段范围与每分钟调用上限；
- 本地授权中心的实现形态：托盘程序、localhost 页面或远程桌面内桌面应用；
- 审计日志保留时长及诊断数据脱敏规范；
- 何时以及在何种审批条件下开放写入工具。
