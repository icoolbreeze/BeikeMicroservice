# property_verification

房源信息验证微服务 —— BeikeMicroservice 平台中的一个**独立服务**。

> 本服务只是平台微服务集合中的一员，不代表整个仓库。

## 未来职责

- 接收不动产权证图片；
- 提取业务件号、证件编码（OCR）；
- 调用**合法授权**的房源信息验证渠道；
- 解析验证结果；
- 生成查询结果截图。

## 当前状态

仅结构骨架：不包含任何业务实现 —— 不接入 OCR、不实现网页自动化、
不访问任何网站、不调用外部接口、不创建数据库表。

## 目录结构

- `app/api`：HTTP 接口（v1 端点：health / extraction / verification / jobs）
- `app/application`：用例编排（命令、查询、应用服务）
- `app/domain`：实体、值对象、仓储与 Provider 抽象
- `app/infrastructure`：配置、数据库、OCR、验证渠道、存储、任务适配
- `app/schemas`：API 数据结构
- `app/security`：脱敏、文件校验、审计
- `tests/`：单元与集成测试（素材规范见 `tests/fixtures/README.md`）
- `storage/`：上传文件、截图、临时文件（运行期生成，不入库）

## 独立交付

本服务可独立开发、测试、配置（`.env.example`）、构建镜像（`Dockerfile`）与部署；
与其他服务通过 HTTP 或消息队列通信（当前未实现）。

## 开发

- 约定见仓库根 `docs/service-development-guide.md` 与 `docs/api-conventions.md`；
- 安全与隐私要求见 `docs/security.md`、`docs/privacy.md`。
