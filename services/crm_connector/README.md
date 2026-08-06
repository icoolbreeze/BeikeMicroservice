# crm_connector

员工专属的 CRM MCP Connector 服务骨架。它将语义化的 CRM 查询工具与认证/会话提供器隔离，默认不包含任何真实凭据（Cookie、Token、Ticket、二维码 Payload、员工 UCID）。`Settings` 的默认 `crm_login_base` 等指向厂商公开 SSO 主机（`login.ke.com`、`lease-pz.link.lianjia.com`），属厂商公开域名而非部署机密；覆盖方式见 `.env.example` 中的 `CC_*` 变量。

## 当前能力

- `/api/v1/health`：服务健康检查；
- `/api/v1/connection/status`：认证与连接状态；
- `/api/v1/mcp/tools`：待 MCP transport 暴露的工具元数据；
- `/api/v1/modules`：CRM 导航模块及实现状态；
- `/api/v1/crm/me`、`/api/v1/listings/rental/search`：已完成租赁房源的分层与输入/输出契约，等待受控的上游路由和会话提供器接入。

默认运行时使用未配置实现，因此业务查询会返回结构化的 `CRM_AUTH_REQUIRED` 或 `CRM_UPSTREAM_NOT_CONFIGURED`，不会尝试任何外部请求。

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
- `infrastructure/` 只提供默认的未配置实现；
- `mcp/tools.py` 定义首期租赁 MCP tool 契约，实际 MCP transport 在后续接入。

认证流程固定为：`CredentialBootstrapProvider → CRM 主体验证 → CredentialStore → SessionProvider.authorizedFetch()`。
业务 Adapter 只能通过 `SessionProvider.authorizedFetch()` 发起已认证请求。不得将认证材料存入 SQLite、普通文件、环境变量、日志、MCP 响应或前端页面。

详细架构与实施计划见 [`docs/personal-crm-mcp-connector-plan.md`](../../docs/personal-crm-mcp-connector-plan.md)。

认证流程实测结果与协议固化见 [`docs/crm-auth-flow-analysis.md`](../../docs/crm-auth-flow-analysis.md)：覆盖 `authentication/initialize`、`qrcode/query`（CREATED→BINDING→CONFIRMED→EXPIRED 状态机）、CONFIRMED 后 `lease-pz.link.lianjia.com/login?...&ticket=...` 的 cookie 注入、业务域受控路由 `GET /api/houseList/search/pc/list` 的请求与响应契约。该文档不含任何真实凭据，是后续 `CredentialBootstrapProvider`、`SessionProvider.authorizedFetch`、CRM HTTP Adapter 实现的事基准。
