# BeikeMicroservice

面向房产经纪公司内部工具的多服务 Monorepo。每个业务服务独立开发、测试、构建和部署，共享工程规范而不共享业务数据。

## 当前服务

| 服务 | 端口 | 状态 | 能力 |
| --- | --- | --- | --- |
| `property_verification` | 8000 | 开发中 | 房源证件字段提取与授权渠道核验 |
| `store_media` | 8010 | 可运行 | 门店图片/视频发布、房源轮播、区域与门店级 RBAC |
| `crm_connector` | 8020 | 开发中 | 员工专属 CRM MCP Connector，封装 ke.com SSO 认证与租赁房源查询；另附 `crm-authd` CLI 于本地 `8021` 端口管理认证 |

`store_media` 的管理页面位于 `/`，门店展示页面位于 `/display.html?store_id=<门店标识>`。详见 [服务说明](services/store_media/README.md)。
`crm_connector` 的认证链路、`crm-authd` CLI 与端点契约详见 [服务说明](services/crm_connector/README.md)。

## 仓库结构

| 目录 | 说明 |
| --- | --- |
| `services/` | 独立业务微服务及机器可读的 `catalog.yaml` |
| `packages/` | 无业务含义的公共 Python 包 |
| `gateway/` | 统一入口骨架，投入生产前需要补齐反向代理与统一令牌校验 |
| `workers/` | 通用后台任务消费者骨架 |
| `infrastructure/` | 部署与基础设施说明 |
| `docs/` | 架构、API、安全、隐私和演进决策 |
| `scripts/` | 工程脚本 |
| `tests/` | 跨服务契约和端到端测试 |

## 快速启动门店媒体服务

```powershell
cd services/store_media
$env:SM_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:SM_BOOTSTRAP_ADMIN_PASSWORD="请设置至少8位的随机密码"
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8010
```

也可通过 Docker Compose 启动：

```powershell
$env:SM_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:SM_BOOTSTRAP_ADMIN_PASSWORD="请设置至少8位的随机密码"
docker compose up --build store-media
```

Docker Compose 启动后访问 `http://localhost:8080/`；直接运行 Uvicorn 时仍使用 `8010`。

## 服务器连接

生产服务器的 SSH 入口、首次配置、连接验证和维护命令见 [服务器连接说明](docs/deployment-servers.md)。密钥和生产凭据不存放在本仓库中。

架构扩展原则与分阶段改造计划见 [架构说明](docs/architecture.md) 和 [ADR-0001](docs/adr/0001-evolutionary-platform-architecture.md)。

Hermes 通过微信处理房源任务时的渠道级降噪、回复时机、用户可见内容和投递验收要求，见 [Hermes 微信渠道回复策略](docs/hermes-weixin-response-policy.md)。

## License

内部使用，详见 [LICENSE](LICENSE)。
