# 架构说明

## 定位

BeikeMicroservice 是面向贝壳房产经纪公司内部 A+ 系统的第三方微服务集合，
采用 Monorepo 管理多个相互独立、可逐步扩展的服务。平台本身不是单一的房源查询系统。

## 顶层结构

- `services/`：独立业务微服务（当前：`property_verification`）
- `packages/`：跨服务公共包（`shared_core` / `shared_config` / `shared_logging` / `shared_contracts`）
- `gateway/`：统一 API 入口（预留）
- `workers/`：通用后台任务消费者（预留）
- `infrastructure/`：部署与基础设施说明
- `docs/` / `scripts/` / `tests/`：平台文档、开发脚本、跨服务测试

## 服务内部分层（DDD 风格）

```
api  ->  application  ->  domain  <-  infrastructure
```

- `api`：HTTP 接口与依赖装配，只处理协议转换；
- `application`：用例编排（命令 / 查询 / 应用服务），不包含领域规则；
- `domain`：实体、值对象、仓储抽象、Provider 抽象；**不依赖任何外部框架**；
- `infrastructure`：配置、数据库、OCR、验证渠道、存储、任务队列等适配实现；
- `schemas`：API 输入输出结构；
- `security`：脱敏、上传文件校验、审计。

## 依赖规则

1. **服务之间禁止直接 import 对方内部代码**。服务协作只能通过：
   `packages/` 公共包、HTTP 接口或消息队列（通信机制当前不实现）。
2. `domain` 不依赖 `infrastructure`；由 `infrastructure` 实现 `domain` 定义的抽象接口。
3. `packages/` 只放真正通用的内容（基础异常、结果类型、配置加载、日志格式、
   日志脱敏、事件结构、公共类型），**禁止放入任何具体业务逻辑**。

## 独立交付

每个服务可独立开发、独立测试、独立配置、独立构建 Docker 镜像、独立部署：
服务根目录自带 `pyproject.toml`、`Dockerfile`、`tests/`、`.env.example`。

## 预留能力

- `gateway/`：未来承担统一入口、路由转发、鉴权、限流、服务发现（当前全部不实现）。
- `workers/`：未来运行通用后台任务消费者（当前仅有入口与注册表占位）。
- 服务间通信：规划为 HTTP / 消息队列，当前阶段不实现。
