# crm_connector

员工专属的 CRM MCP Connector 服务。它将语义化的 CRM 查询工具与认证/会话提供器隔离，默认不包含任何真实凭据（Cookie、Token、Ticket、二维码 Payload、员工 UCID）。`Settings` 的默认 `crm_login_base` 等指向厂商公开 SSO 主机（`login.ke.com`、`lease-pz.link.lianjia.com`），属厂商公开域名而非部署机密；覆盖方式见 `.env.example` 中的 `CC_*` 变量。

## 当前能力

> **完整链路已于 2026-08-06 用真实扫码验证打通**：扫码登录 → CAS 双跳 bootstrap → 凭证入库 → `authorizedFetch` 业务调用 → TGC 无感刷新。

- `/api/v1/health`：服务健康检查；
- `/api/v1/connection/status`：认证与连接状态；
- `/api/v1/mcp/tools`：待 MCP transport 暴露的工具元数据；
- `/api/v1/modules`：CRM 导航模块及实现状态；
- `/api/v1/crm/me`、`/api/v1/listings/rental/search`：已完成租赁房源的分层与输入/输出契约，走通真实上游 `KecomCrmClient` → `KecomSessionProvider.authorizedFetch` → `lease-pz.link.lianjia.com`。

### 已验证的端到端链路

| 环节 | 上游端点 | 验证结果 |
| --- | --- | --- |
| 扫码 bootstrap | `POST /authentication/authenticate` → CAS 双跳 | TGC + `puzu_lease_token` + `saas_token` 全部种下 |
| houseList 查询 | `GET /api/houseList/search/pc/list` | `code:100000`，返回 10 条真实房源（例 `ICC凯旋门 月租3850元`） |
| keepalive | `GET /api/puzuHouse/.../accountRightInfo?typeList=2` | `code:100000`，`ConnectionState.READY` |
| TGC 刷新 | CAS 双跳（无扫码） | 新 `puzu_lease_token` + `saas_token` 种入，`BootstrapResult` 正常返回 |

## 本地运行

```powershell
cd services/crm_connector
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8020
```

> Dockerfile 用于服务契约与基础运行验证；生产 Connector 仍应运行在云枢已接入的员工专属 Windows VM 中，不应把云枢或员工认证材料打包进镜像。

## 分层

```text
api -> application -> domain <- infrastructure
```

- `domain/providers/credential_bootstrap_provider.py` 是认证材料引导边界；
- `domain/providers/session_provider.py` 是已认证请求边界；
- `domain/providers/credential_store.py` 是认证材料的唯一持久化边界；
- `domain/providers/crm_client.py` 是 CRM 业务接口边界；
- `infrastructure/kecom_qr_bootstrap.py`：扫码 bootstrap + CAS 双跳 + TGC refresh 实现（`KeComQrBootstrapProvider`）；
- `infrastructure/kecom_session_provider.py`：已认证请求代理与 keepalive，注入 `puzu_lease_token` + `saas_token` 等业务 cookie（`KecomSessionProvider`）；
- `infrastructure/kecom_crm_client.py`：CRM 业务 Adapter，通过 `SessionProvider.authorizedFetch` 调用受控路由（`KecomCrmClient`）；
- `infrastructure/windows_dpapi_credential_store.py`：Windows DPAPI 加密本地存储；
- `mcp/tools.py` 定义首期租赁 MCP tool 契约，实际 MCP transport 在后续接入。

认证流程固定为：`CredentialBootstrapProvider → CRM 主体验证 → CredentialStore → SessionProvider.authorizedFetch()`。
业务 Adapter 只能通过 `SessionProvider.authorizedFetch()` 发起已认证请求。不得将认证材料存入 SQLite、普通文件、环境变量、日志、MCP 响应或前端页面。

### CAS 双跳认证链路

一次扫码完成全部业务调用所需的 cookie 种植：

1. **lease-pz CAS hop**：`GET login.ke.com/login?service=<lease-pz/login?gotoURL=...>` (带 TGC) → 302 → `lease-pz/link?.ticket=ST` → Set-Cookie `puzu_lease_token` / `UCID` / `csrfSecret`。
2. **house.link shiro-cas hop**：`GET login.ke.com/login?service=house.link.lianjia.com/shiro-cas` (同一 TGC) → 302 → `house.link/shiro-cas?ticket=ST` → Set-Cookie `saas_token` / `HOUSEJSESSIONID`。

仅有 `puzu_lease_token` 时 `accountRightInfo`/`houseList` 返回 `100001`（请重新登录）；补上 `saas_token` 后翻成 `100000`。两跳共用同一 `TGC`，零额外扫码。

终端二维码收到上游的过期状态后，`crm-authd login` 会自动重新初始化并展示新二维码。刷新初始等待时间由 `CC_BOOTSTRAP_QRCODE_REFRESH_INITIAL_DELAY_SECONDS` 控制，连续失败时采用最多 30 秒的指数退避。

详细架构与实施计划见 [`docs/personal-crm-mcp-connector-plan.md`](../../docs/personal-crm-mcp-connector-plan.md)。

认证流程实测结果与协议固化见 [`docs/crm-auth-flow-analysis.md`](../../docs/crm-auth-flow-analysis.md)：覆盖 `authentication/initialize`、`qrcode/query`（CREATED→BINDING→CONFIRMED→EXPIRED 状态机）、CONFIRMED 后 `lease-pz.link.lianjia.com/login?...&ticket=...` 的 cookie 注入、业务域受控路由 `GET /api/houseList/search/pc/list` 的请求与响应契约。该文档不含任何真实凭据，是后续 `CredentialBootstrapProvider`、`SessionProvider.authorizedFetch`、CRM HTTP Adapter 实现的事基准。
