# 服务注册表

所有服务先登记到机器可读的 [`services/catalog.yaml`](../services/catalog.yaml)，本文档提供面向开发者的摘要。

| 服务 | 路径 | 成熟度 | 数据所有权 | 说明 |
| --- | --- | --- | --- | --- |
| property_verification | `services/property_verification` | development | 核验任务及输出 | 房源信息提取与核验 |
| store_media | `services/store_media` | runnable | 门店、账号、会话、媒体元数据与文件 | 门店媒体发布、房源轮播、范围化 RBAC |
| crm_connector | `services/crm_connector` | development | 员工范围的 Connector 配置与脱敏审计摘要 | 个人 CRM MCP Connector 服务骨架 |

新增服务必须声明：服务所有者、业务边界、健康检查、配置前缀、持久化数据、对外 API 前缀和依赖。服务之间禁止直接读取对方数据库或上传目录。

## 规划中的候选服务

`identity_access`、`image_processing`、`document_extraction`、`data_sync` 和 `notification`。其中统一身份服务应在第二个业务服务需要登录或接入企业 SSO 时启动拆分，而不是提前复制账号表。
