# CRM 认证流程分析（实测）

- 状态：事实已收集，运行时验证有限
- 日期：2026-08-06
- 适用：`services/crm_connector` 的 `CredentialBootstrapProvider`、`CredentialStore`、`SessionProvider`、CRM HTTP Adapter 实现
- 关联：[`personal-crm-mcp-connector-plan.md`](personal-crm-mcp-connector-plan.md)、[`security.md`](security.md)

> 本文不写入任何真实凭据。Token、Cookie 值、二维码 ID、`loginTicketId`、用户主体等运行时值一律用占位符表示。文档只固化协议形态、字段名、状态机和实现指引。

## 1. 背景与方法

`crm_connector` 的首期目标是用 CRM 既有权限内的租赁只读查询驱动 Agent。CRM 网页登录入口为 `login.ke.com`（贝壳/链家 CAS），业务域为 `lease-pz.link.lianjia.com`（普租 v2）。本次分析通过两次完整的真人扫码登录、CDP 网络抓包、同源 iframe SDK 源码检索，复现并验证了从 `initialize` 到业务域 cookie 注入的全部协议节点。

测试期间通过 CDP 拦截到的请求包括 `authentication/initialize`、`qrcode/query`、`lease-pz.link.lianjia.com/login?...&ticket=...`、`login.ke.com/login?service=...`、`house.link.lianjia.com/shiro-cas?ticket=...`，以及业务域受控 API `GET /api/houseList/search/pc/list`。两个浏览器隔离 context (crm-auth-probe、crm-auth-qr2) 分别完成一次扫码 + 手机端确认 + 跳转落地，结果一致。

## 2. 顶层认证协议

CRM 使用 CAS 风格的 SSO，与 OAuth Bearer 无关。鉴权全部基于 HttpOnly Cookie。一次成功的扫码登录分四段：

### 2.1 `POST /authentication/initialize`（拿二维码与 loginTicket）

- 完整 URL：`https://login.ke.com/authentication/initialize`
- 请求体：
  ```json
  {
    "service": "https://lease-pz.link.lianjia.com/login?gotoURL=%252F",
    "context": { "deviceId": "default", "sign": "default" },
    "version": "2.0"
  }
  ```
- 请求头：无认证 Cookie；可携带 ke.com 上 anonymous 埋点 cookie (`lianjia_ssid`, `lianjia_uuid`, `crosSdkDT2019DeviceId`)，但不必须。
- 200 响应体字段（Schema 已固化）：
  ```jsonc
  {
    "success": true,
    "loginTicketId": "<短字符串>",                // 用于 USERNAME/PASSWORD 链路，扫码链路不直接消费
    "authenticationMethods": {
      "employee": [
        { "type": "oauth-employee", "allianceMethods": [], "initialOptions": {} },
        { "type": "third-party-employee", "allianceMethods": [], "initialOptions": {} },
        {
          "type": "qrcode",                          // ★ 扫码方法固定为 "qrcode"
          "allianceMethods": [],
          "initialOptions": {
            "id": "<二维码 ID>",                    // ★ 用于 qrcode/query
            "qrCodeContent": "https://t.lianjia.com/<短码>"  // ★ Link/A+/D+/Studio/企微扫描目标
          }
        },
        { "type": "automatic-token", "allianceMethods": [], "initialOptions": {} },
        {
          "type": "username-password",
          "allianceMethods": [
            { "type": "security-code", "initialOptions": {} },
            { "type": "shield-code", "initialOptions": {} }
          ],
          "initialOptions": {}
        },
        { "type": "shanghai-sso", "allianceMethods": [], "initialOptions": {} }
      ],
      "customer": [ ... ]
    },
    "publicKey": {
      "appEncrypt": "true",
      "version": "1",
      "key": "<RSA 公钥 DER Base64>"            // 仅账号密码登录用于加密密码，扫码链路忽略
    },
    "supportedAccountSystems": [
      { "id": "customer", "name": "<客户>",  "viewStyle": { "register-entry": "/register" } },
      { "id": "employee",  "name": "<员工>", "viewStyle": {} }
    ]
  }
  ```
- 用法：从 `authenticationMethods.employee[]` 中查 `type == "qrcode"`，取 `initialOptions.id` 与 `qrCodeContent`。

### 2.2 渲染二维码（人工扫码，不可绕过）

`qrCodeContent` 是一条 `https://t.lianjia.com/<6位短码>` 链接，员工用 Link App 或 A+/D+/Studio/企微扫描该短链，在手机端确认登录。重新生成二维码（"刷新"操作）会调用另一次 `POST /authentication/initialize`，得到新的 `id` 与 `qrCodeContent`；旧 `id` 立即失效。

实现侧：Connector 在 `crm-authd` 授权中心用任意二维码渲染库（Python `qrcode`、`segno` 或终端 `qrcode-terminal`）把 `qrCodeContent` 短链渲染为可扫图片/ASCII 二维码，展示给绑定的员工。

### 2.3 `GET /authentication/qrcode/query?id=<二维码ID>`（轮询）

- URL 查询参数：`id` = `initialOptions.id`。
- 请求头：无 Authorization；携带 `initialize` 阶段下发的 `lianjia_ssid`。
- 200 响应体（Schema 已固化）：
  ```jsonc
  { "state": "<状态字符串>", "success": true }
  ```
- 实测状态机：
  - `CREATED` → 二维码已生成，未扫
  - `BINDING` → 已扫，手机端尚未确认（实测保持 60s+，由手机端用户决定时长）
  - `CONFIRMED` → 已确认，响应带跳转用 ticket（见 2.4）
  - `EXPIRED` → 二维码过期，需"刷新"
- 轮询节流：实测 Web SDK 约 3s 间隔；Connector 实现应取 3–5s，并对 `EXPIRED` 重试 30s 指数退避后再调 `initialize`。
- ⚠️ **未直接抓到 CONFIRMED 响应体**：第一轮测试 Chrome Network 未保留 iframe 内的 XHR；第二轮在 `__qrLogs` 中捕获到 `CREATED` → `BINDING` 全过程，但因为 CONFIRMED 后 SDK 立即触发 `window.location.href` 跳转，iframe 被销毁，`__qrLogs` 归零。下文 2.4 通过**第二次 CONFIRMED 后的 referer 反推**得到了与 2.4 一致的事实。

### 2.4 CONFIRMED → 跳转 `lease-pz.link.lianjia.com/login?...&ticket=...`（换 cookie）

CONFIRMED 触发 SDK 用 `service` 参数 + ticket 拼装跳转 URL：

```
https://lease-pz.link.lianjia.com/login?gotoURL=%252F&ticket=ST-<数字>-<base62>-ke.com
```

- `ticket` 形如 `ST-<数字>-<base62 串>-ke.com`，由 CAS Server 颁发的一次性 service ticket。
- SDK 直接 `window.location.href = service + "?gotoURL=" + escape(goto) + "&ticket=" + ticket`，无需额外签名或 state。
- 该请求由浏览器顶层导航触发；**响应通过 `Set-Cookie` 注入业务域全部 HttpOnly 认证 cookie**（见 §3 表）。
- 落地 URL 在实例中是 `https://lease-pz.link.lianjia.com/rent/house/list?isSaaS=false`。

## 3. 凭据集合（Cookie 集）

下表为业务域 HttpOnly 与 ke.com 域 SSO 续航相关 Cookie。**真实值未保存到仓库/日志/文档**。

| Cookie | 域 | HttpOnly | 作用 | Connector 处置 |
| --- | --- | --- | --- | --- |
| `puzu_lease_token` / `puzu_lease_token_secure` | `lease-pz.link.lianjia.com` | 是 | 业务域核心 bearer-like token | ★ 必存入 `credential_material` |
| `UCID` / `UCID_secure` | `lease-pz.link.lianjia.com` | 是 | 用户唯一主体标识（10 位数字） | ★ `employee_principal` 来源；纳入 `credential_material` |
| `csrfSecret` | `lease-pz.link.lianjia.com` | 是 | 令牌，POST 时服务端实测未强校验 | 纳入 `credential_material`（兼容保留） |
| `Lianjia_u_info` | `lease-pz.link.lianjia.com` | 是 | URL 编码的姓名/工号/门店/公司 | 可重建，仍随 cookie 一同保存 |
| `Lianjia_curWorkCity` / `Lianjia_BUcid` | `lease-pz.link.lianjia.com` | 是 | 当前工作城市 / 主体 | 纳入 `credential_material` |
| `lianjia_ssid` | `.lianjia.com` / `.ke.com` | 否 | 30 分钟滑窗会话 ID，每次响应刷新 `Max-Age=1800` | `session_id` 候选；用于活性探测 |
| `lianjia_uuid` | `.lianjia.com` | 否 | 设备埋点 | 保留以最大化兼容 |
| `saas_token` | `lease-pz.link.lianjia.com` | 否 | SaaS 层会话 token | 保留以最大化兼容 |
| `login_ucid` | `login.ke.com` | 否 | ke.com 域已知 UCID | 用于 SSO 续航识别 |
| `TGC` / `TGC_Secure` | `login.ke.com` | 是 | CAS Ticket Granting Cookie；可反复为任意 service 签发 ST ticket | ★ 续期关键（见 §5），不直接用于业务调用 |
| `security_ticket` | `login.ke.com` | 是 | ke.com 域辅助签名/续期 | 同上 |

### 最小必需 vs 兼容集

实测（同源 fetch 上下文内）仅依赖"浏览器当前 cookie"，不存在 Authorization header。POST `/api/puzuHouse/puzu/house/auth/switchList` 在不带额外 CSRF 头时返回业务级错误 `key列表不能为空`（`code:100001`），证明 csrfSecret 并非强双提交。

Connector 的 `authorizedFetch` 推荐这样的最小集策略：

- **必需**：`puzu_lease_token` (+`_secure`)、`UCID` (+`_secure`)、`csrfSecret`。
- **兼容附带**：`Lianjia_u_info`、`Lianjia_curWorkCity`、`Lianjia_BUcid`、`lianjia_ssid`、`lianjia_uuid`、`saas_token`。
- **非业务路径**：建议**不要**携带 `sensorsdata2015jssdkcross`、`sajssdk_2015_cross_new_user`、`autoLogout` 等埋点 cookie，以减少审计面。
- **续期独占**：`TGC`、`TGC_Secure`、`security_ticket` 由 `crm-authd` 单独持有，不进入业务 `SessionProvider.authorizedFetch` 的 cookie jar。

## 4. 业务请求受控契约

本次抓到的业务 API 调用契约（节选）：

| 方法 | 路径 | 用途 | 备注 |
| --- | --- | --- | --- |
| GET | `/api/houseList/search/pc/list` | 租赁房源分页搜索 | 首期 `rental_listing.search` 的实际端点 |
| POST | `/api/puzuHouse/puzu/house/auth/switchList` | 房源切换列表 | 实测 CSRF 校验宽松 |
| GET | `/api/houseList/search/listSecondLevelTag` | 二级标签 | 启动配置，**不**依赖用户授权 |
| POST | `/api/header/headerData` | 头部用户上下文 | 见 req-134 |
| GET | `/api/houseList/search/tab` | Tab 配置 | 启动配置 |
| GET | `/api/houseList/search/pc/searchOption` | 搜索选项 | 启动配置 |
| GET | `/api/puzuHouse/puzu/house/auth/pc/accountRightInfo?typeList=2` | 账号权限信息 | 等同 `crm_whoami` 来源 |
| GET | `/api/task/special/activity/functionSwitch?cityCode=...&ucid=...` | 功能开关 | `ucid` 在 URL，体现主体绑定 |

### `GET /api/houseList/search/pc/list`（已验证）

请求必需项：

- 路由别名（`rental_listing.search`）
- 查询参数：`pageSize`、`pageIndex`、`relationRange=1`、`sceneCode=puzu_mix_list_pc`、`clientOsType=3`
- 请求头：`x-requested-with: XMLHttpRequest`、`house_current_work_citycode: <城市码>`（实例值 `510100`，成都）
- Cookie：见 §3 "必需集合"

响应（已固化，示例字段名）：

```jsonc
{
  "code": 100000,                                    // 100000 = 加载成功；403 = 用户未登录；100001 = 业务参数错
  "msg": "<中文提示>",
  "data": {
    "list": [
      {
        "delCode": "<房源编号>",                       // ★ 二次详情查询的 key
        "standardHouseId": <long>,
        "resblockId": <long>, "resblockName": "<小区名>",
        "area": <float m²>,
        "bedroomAmount": <int>, "hallAmount": <int>, "bathroomAmount": <int>, "kitchenAmount": <int>,
        "price": <int 月租金元>,
        "rentType": "001",                            // 整租001等
        "orientation": ["<朝向>"],
        "floorLevel": "<楼层类型>", "totalFloor": <int>,
        "haveKey": <bool>,
        "fitmentStatus": "003", "fitmentStatusDesc": "<装修>",
        "bizCircleName": "<商圈>",
        "titleImage": "<缩略图 URL>",
        "floorPlanImage": "<户型图 URL>",
        "tagList": [{ "type":"<id>", "desc":"<文案>", "backgroundColor":"#", "textColor":"#" }],
        "mapTitle": "<地图标题>", "mapArea":"<面积字符串>", "mapPrice":"<价格字符串>",
        "maintainUcId": <long>, "maintainUcName": "<维护人姓名>",
        "checkinTime": <ms>, "checkinTimeDesc": "<可入住时间>",
        "haoFangScore": <float>, "hqiScore": <int>, "vrStatus": <0|1>,
        "createTime": <ms>, "createTimeDesc": "<相对时间>",
        "extranetAppear": <bool>
        // …其他字段省略，解析时使用 Schema 校验并最小化输出
      }
    ],
    "totalCount": <int>, "totalPage": <int>, "startRow": 0, "validInThread": true
  },
  "traceId": "<trace>", "brand": "ALLIANCE", "nodeRequestId": "<uuid>"
}
```

### 错误码与状态映射（部分已实测）

| 业务 code | 含义 | Connector 状态 |
| --- | --- | --- |
| `100000` | 加载成功 | `Ready` |
| `403` + `用户未登录` | 缺失/失效业务 cookie | `CRM_AUTH_REQUIRED`，触发 bootstrap 或 refresh |
| `100001` | 业务参数错（例 `key列表不能为空`） | `CRM_UPSTREAM_INVALID_INPUT`，不重试 |
| 未知 4xx / 5xx | 上游不可用或契约变化 | `CRM_UPSTREAM_CHANGED`，记录脱敏诊断 |

## 5. 续期（refresh）路径

### 5.1 已观察到的 CAS 二次签发（不需再次扫码）

业务落地后，页面会触发对 `house.link.lianjia.com/search/...` 的访问，引发：

```
GET https://login.ke.com/login?service=https://house.link.lianjia.com/shiro-cas
Cookie: TGC=<TGT-...-ke.com>; TGC_Secure=<TGT-...>; security_ticket=<...>; login_ucid=<UCID>
→ 302 Location: https://house.link.lianjia.com/shiro-cas?ticket=ST-<数字>-<base62>-ke.com
```

即 **只要 ke.com 域持有 `TGC`+`TGC_Secure`+`security_ticket`，就可以反复为任意 service 签发一次性 ST ticket，无需重复扫码**。这意味着：

- Connector 的 `bootstrap()` 在首次扫码拿到 cookie 后，应同时把 ke.com 域的 SSO 续航 cookie 也纳入持久化（仅 `crm-authd` 持有）。
- `refresh(current)` 实现可以是：
  1. `GET https://login.ke.com/login?service=https://lease-pz.link.lianjia.com/login?gotoURL=%252F`（带 TGC），从 302 Location 取新 `ticket`。
  2. `GET https://lease-pz.link.lianjia.com/login?gotoURL=%252F&ticket=<新ST>`，从 `Set-Cookie` 提取业务域受保护 cookie，替换 `credential_material`。
  3. 失败（302 仍指向登录页、cookie 缺失）则降级为 `CRM_AUTH_REQUIRED`，要求员工再扫码。

### 5.2 待在云枢环境验证的边界

下列情况在本机的 CDP 测试中无法验证，必须迁到已接入云枢的 VM、用 Python httpx 实施 A 路径重放才能确认：

- `TGC` / `security_ticket` 是否长期有效，过期时间或刷新触发条件；
- `puzu_lease_token` 实际过期时间（推测数小时）；与 `lianjia_ssid` 滑窗 30 分钟的关系；
- 仅 `TGC` + `security_ticket` 是否足以触发 refresh；是否存在与 ke.com 服务端的 sliding session 信号；
- 若 refresh 失败，CAS 是否会要求二次扫码（即降级回 §2 全流程）；
- 数美风控 (`public-digc.ke.com/h5/v4/cf`、`/h5/v2/g`) 在非浏览器客户端是否影响结果。浏览器内抓到这两个调用，是埋点而非签名接口；最坏情况下 Python 客户端不带这些 cookie 也能完成 SSO，但需实测确认。

## 6. 主体验证（`crm_whoami`）

发现 `accountRightInfo` 与业务 API 的 URL 参数已存在主体绑定事实：

```
GET /api/puzuHouse/puzu/house/auth/pc/accountRightInfo?typeList=2
GET /api/task/special/activity/functionSwitch?cityCode=<城市>&ucid=<UCID>
GET https://bms.lease.ke.com/api/config/operation/retrieve/config
    ?ucid=<UCID>&vRole=jingjiren&vOrgCode=<门店orgCode>&channelType=1&sectionKey=...
```

可解析 `accountRightInfo` 响应或用 `/api/header/headerData` 等返回的 UCID/姓名/门店/角色字段，作为 `PrincipalContext`。`Lianjia_u_info` cookie 已是 URL 编码形式的 `{id, name, orgCode, usercode, companyCode, officeAddress}`。

Connector 必须在 `whoami()` 后比较响应中的 `ucid` 与 `BOUND_EMPLOYEE_PRINCIPAL`（即 `UCID`），不一致则 `ConnectorDegradedError`。

## 7. 对 `crm_connector` 实现的指引

### 7.1 `domain/providers/credential_bootstrap_provider.py`

接口已定义（`bootstrap`、`refresh`、`validate`、`revoke`）。新增 `infrastructure/kecom_qr_bootstrap.py`：

- `bootstrap()`: `httpx` 调 §2.1 → 渲染 `qrCodeContent` → 等待人工扫码 → 轮询 §2.3 → 取 `CONFIRMED` 响应里的 `ticket` → §2.4 换 cookie → 同时保存 ke.com 域 SSO 续航 cookie（TGC 等）到受保护存储 → 返回 `BootstrapResult(credential_material=<业务 cookie jar bytes>, expires_at=<lng>, credential_version=<int>)`。
- `refresh(current)`: 用持久化的 TGC cookie 调 §5.1 Step 1–2 → 重新拿到 cookie → 返回新的 `BootstrapResult`；失败返回 `None` 或抛错。
- `validate(credential_material)`: 解析 cookie jar → 调 `accountRightInfo` 等接口 → 返回 `Principal`（`employee_principal=UCID`）。
- `revoke(current)`: 调 `POST https://login.ke.com/logout`（待实测）使 TGC 失效，同时 `CredentialStore.invalidate`。

`httpx` 请求必须：
- 设置合理超时（init 10s、polling 3s、ticket 换 cookie 10s）；
- 跟随重定向手动控制，每次跳转保留 cookie jar 与对应域的 HttpOnly；
- 不打印 `credential_material`、不写日志（包括 set-cookie 原文），仅在 `crm-authd` 进程内透传给 `CredentialStore`。

### 7.2 `domain/providers/credential_store.py`

`ActiveCredential.credential_material` 字段语义应明确为"业务域受保护 cookie jar 的序列化（pickle 或 JSON）"，**不**包含 ke.com 域 SSO 续航 cookie；TGC 等单独存为 `ActiveCredential.refresh_material`（或在 `crm-authd` 自有的分隔槽位），始终不暴露给 `SessionProvider`。

Windows 受保护存储：
- 首选 `win32crypt.CryptProtectData`（DPAPI）+ 本地文件；
- 备选 `keyring` (`Windows Credential Vault`)；
- 严禁明文落盘、严禁进 SQLite、严禁写入审计日志。

### 7.3 `infrastructure/crm_http_adapter.py`（新增）

实现 `CrmClient` 协议，内部走 `SessionProvider.authorizedFetch(AuthorizedRequest)`：

- `AuthorizedRequest.route` 使用路由别名（`identity.me`、`rental_listing.search`、`rental_listing.get_detail`）；
- adapter 持有路由注册表（`CrmRoute`）：把别名映射到上游 method + path + 允许参数 + 响应 Schema + 错误码映射；
- 真实上游地址不写入代码，由 `CRM_ROUTE_PROFILE` 注入（生产值仅在云枢 VM 的本地受保护配置中）。

### 7.4 MCP 工具映射

`mcp/tools.py` 当前已经定义工具契约；首期三个工具映射到受控路由：

- `crm_connection_status` → `SessionProvider.status()` + 云枢连通探测。
- `crm_whoami` → `CrmClient.whoami()`（结果与 `BOUND_EMPLOYEE_PRINCIPAL` 比对）。
- `rental_listing_search` → `CrmClient.search_rental_listings()`，输入 Schema 对齐 §4 字段，输出最小化（去掉埋点字段、保持必要业务字段）。
- `rental_listing_get_detail` → 上游待抓（推测 `/api/houseList/search/pc/list/{delCode}` 或 `/api/puzu/house/...`），需在云枢环境补抓后写契约。

### 7.5 测试策略

- 单元测试：用合成 cookie 与请求/响应 fixture（无真实值）回放协议，覆盖 `CREATED → BINDING → CONFIRMED → EXPIRED` 全状态机。
- 集成测试：跳过真实 ke.com（不在 CI 网络可达范围），仅覆盖"已注入 cookie 后的 `authorizedFetch` → CRM 适配 → Schema 校验"路径。
- 在云枢 VM 上做一次性 smokedog 比对：Connector 与浏览器对同一查询条件的结果集逐项比对，确认无字段缺失或权限差异。

## 8. 已知未知项（下一轮需在云枢环境验证）

1. `puzu_lease_token` 的过期时间与刷新触发条件；
2. 仅 `TGC`+`security_ticket` 是否足以无浏览器 refresh；
3. `ticket` 字段在 CONFIRMED 响应体中的真实字段名（`ticket`？`st`？`redirectUrl`？）—— 可用 `mitmproxy` 或一台 LICEE 授权的工具在云枢 VM 上重放捕获，但绝不在仓库或日志中保留样本；
4. `rental_listing.get_detail` 的真实上游路由、请求与响应 Schema；
5. 是否存在未观察到的设备指纹强校验（`public-digc.ke.com` 的 `/h5/v4/cf`、`/h5/v2/g`）；
6. logout/`revoke` 的真实 Cookie 失效行为（`POST /logout` 是否清除 TGC）。

## 9. 安全约束（与前述文档对齐）

- 仓库、日志、镜像、测试夹具中**禁止**保存真实 cookie、ticket、二维码内容、`loginTicketId`；
- 本文档所有"占位符"若被误填为真实值，必须立即重写并审计 git 历史；
- Connector 必须运行在云枢已连接的员工 VM 内；云枢不可达时所有业务工具返回 `CRM_NETWORK_REQUIRED`；
- `credential_material` 永远不通过 MCP、REST 响应、授权中心页面或日志暴露，仅返回状态与 `expires_at` 元数据。

---

附录 A：测试时间线（UTC）

- T+0：打开 `login.ke.com`，发现已有旧 session，自动跳转 lease 业务首页；
- T+t：抓取业务首页与受控 API `pc/list`，固化响应 Schema；
- T+t+：清 JS 可见 cookie（HttpOnly 无法清）；
- T+t+：开 `crm-auth-probe` 隔离 context，员工扫码选择，进入二维码扫码登录页；
- T+t+：捕获 `authentication/initialize` 与 `qrcode/query`（CREATED 与 EXPIRED）；
- T+t+：员工完成扫码 + 手机确认，捕获 lease-login 跳转 referer 中的 ST ticket；
- T+t+：开 `crm-auth-qr2` 隔离 context 重做扫码链；在 iframe 同源 window hook `fetch`/`XHR`，捕获 `CREATED → BINDING → CONFIRMED` 全状态机（CONFIRMED 后 iframe 销毁、`__qrLogs` 归零，但顶层 `window.location.href` 的最终 referer 完整证实跳转结构与 ticket）。
