# crm_connector

员工专属的 CRM MCP Connector 服务。它将语义化的 CRM 查询工具与认证/会话提供器隔离，默认不包含任何真实凭据（Cookie、Token、Ticket、二维码 Payload、员工 UCID）。`Settings` 的默认 `crm_login_base` 等指向厂商公开 SSO 主机（`login.ke.com`、`lease-pz.link.lianjia.com`），属厂商公开域名而非部署机密；覆盖方式见 `.env.example` 中的 `CC_*` 变量。

## 当前能力

> **完整链路已于 2026-08-06 用真实扫码验证打通**：扫码登录 → CAS 双跳 bootstrap → 凭证入库 → `authorizedFetch` 业务调用 → TGC 无感刷新。
> **MCP stdio 已于 2026-08-07 用真实凭据验证**：`crm-mcp` 子进程在 `kecom-prod` profile 下通过 `stdio` 暴露 4 个工具，`crm_whoami` / `rental_listing_search` / `rental_listing_get_detail` 均返回真实 CRM 数据。

- `/api/v1/health`：服务健康检查；
- `/api/v1/connection/status`：认证与连接状态；
- `/api/v1/mcp/tools`：与 MCP transport 同源的工具元数据（`app/mcp/tools.py`，经 `/tools/list` 暴露）；
- `/api/v1/crm/me`、`/api/v1/listings/rental/search`：已完成租赁房源的分层与输入/输出契约，走通真实上游 `KecomCrmClient` → `KecomSessionProvider.authorizedFetch` → `lease-pz.link.lianjia.com`。
- `/api/v1/listings/rental/{listing_id}`：租赁房源详情端点，返回单条 `RentalListingResponse`。

## crm-authd / crm-mcp 命令行

本服务除 FastAPI 主服务（默认 `8020`）外，还提供两个命令行入口：

- `crm-authd`：管理员工 CRM 认证材料并在本地保持会话，入口在 `pyproject.toml` 注册为 `crm-authd = "app.authd.cli:main"`，实现见 `app/authd/cli.py`；
- `crm-mcp`：MCP stdio 服务器，入口注册为 `crm-mcp = "app.mcp.server:main"`，实现见 `app/mcp/server.py`。

| 命令 | 作用 |
| --- | --- |
| `crm-authd login` | 触发扫码登录引导（弹出二维码窗口），拿到上游凭证后用 Windows DPAPI 写入 `CC_CREDENTIAL_STORE_PATH`，并校验 `CC_BOUND_EMPLOYEE_PRINCIPAL` 是否与扫码主体一致 |
| `crm-authd status` | 读取本地凭证并打印 `state`（`ready` / `expiring` / `auth_required`）、会话主体、过期时间、`credential_version` |
| `crm-authd logout` | 调用 `bootstrap.revoke` 失效上游会话并清空本地凭证记录 |
| `crm-authd serve` | 在 `CC_AUTHD_LISTEN_ADDRESS`（默认 `127.0.0.1:8021`）启动本地认证中心 HTTP 服务，并在同进程内运行 keepalive 后台线程 |
| `crm-mcp` | 在 `stdio` 上运行 MCP server（`app/mcp/server.py` 的 `main()`），按 `CC_UPSTREAM_PROFILE` 接线真实或 stub provider |

`crm-authd serve` 暴露的端点（前缀 `/api/v1/auth`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/auth/status` | 当前认证状态、绑定员工、过期时间、最近一次 keepalive |
| GET | `/api/v1/auth/poll` | 与 `status` 一致，为后续接入 SSE / tray UI 预留 |
| GET | `/api/v1/auth/keepalive` | 手动触发一次 TGC 无感刷新并返回结果 |
| GET | `/api/v1/auth/notify` | 预留给未来 tray UI 向 authd 推送通知（当前镜像 status） |

> 认证材料只存放在 `CC_CREDENTIAL_STORE_PATH`（默认 `./run/credential_store.bin`），用 Windows DPAPI 加密；`crm-authd` 之外的主服务进程通过 `SessionProvider.authorizedFetch()` 间接使用，从不直接接触原始 cookie/token。

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

默认 `CC_UPSTREAM_PROFILE=unconfigured`，主服务会以 stub provider 启动，可在 CI/开发环境直接跑通。接入真实 CRM 上游的方法见 `.env.example`：先把 `CC_UPSTREAM_PROFILE` 改为非 `unconfigured`，再在单独的 PowerShell 中执行 `crm-authd login` 完成扫码引导，凭证入库后用 `crm-authd serve`（监听 `127.0.0.1:8021`）保持会话。

## MCP 接入

`crm-mcp` 以 stdio 运行 MCP server，供本地 Agent（Claude Code 等）直接调用：

```json
{
  "mcpServers": {
    "crm-connector": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "C:\\path\\to\\services\\crm_connector",
      "env": {
        "CC_UPSTREAM_PROFILE": "kecom-prod",
        "CC_BOUND_EMPLOYEE_PRINCIPAL": "<扫码主体>",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

- 工具清单（全部只读）：`crm_connection_status`、`crm_whoami`、`rental_listing_search`、`rental_listing_get_detail`；
- `crm_connection_status` 不限额；其余 3 个按调用者主体（`getpass.getuser()`）受 `CC_MCP_RATE_LIMIT_PER_MIN`（默认 30 次/分钟）滑动窗口限流，超限返回 `RATE_LIMITED`；
- 未配置凭据时业务工具返回 `CRM_AUTH_REQUIRED`，不会访问上游；
- 凭据只经 `SessionProvider.authorizedFetch()` 注入，MCP 响应与日志不含任何认证材料。

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
- `mcp/tools.py`：首期 4 个 MCP tool 的契约（输入/输出 Schema、只读标注），与 HTTP `/api/v1/mcp/tools` 同源；
- `mcp/server.py`：MCP server 组装（`build_mcp_server`）与 `stdio` 入口（`main`），`MCPServer.tool` 注册 4 个工具、`ToolAnnotations(read_only_hint=True)` 只读标注、`_require_quota` 按调用者主体限流；
- `mcp/schemas.py`：MCP 入参模型（`extra="forbid"` 拒绝未知字段）；
- `mcp/rate_limit.py`：滑动窗口工具级限流。

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
