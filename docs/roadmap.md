# 路线图

## 阶段 0：平台结构（当前）

- Monorepo 骨架：`services/`、`packages/`、`gateway/`、`workers/`、
  `infrastructure/`、`docs/`、`scripts/`、`tests/`；
- 首个服务 `property_verification` 的目录与接口占位；
- 平台文档与约定。**不含任何业务实现。**

## 阶段 1：property_verification 基础实现

- 配置加载、健康检查、任务实体与仓储（内存/数据库实现选型）；
- 提取任务与验证任务的创建/查询接口骨架打通。

## 阶段 2：验证能力对接

- OCR 能力选型与适配（`infrastructure/ocr`）；
- 合法授权验证渠道对接（`infrastructure/verification`）；
- 结果解析与查询截图生成。

## 阶段 3：平台能力接入

- `gateway` 统一入口（路由转发、鉴权、限流）；
- `workers` 通用后台任务消费者接入任务队列；
- `infrastructure/` 落地部署配置。

## 阶段 4：服务扩展

- 按 `docs/service-registry.md` 规划逐步新增独立服务
  （如 image_processing、document_extraction、data_sync 等）。
