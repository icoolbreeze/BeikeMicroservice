# 微服务开发指南

## 新增一个服务

```bash
python scripts/create_service.py my_service
# 或
make new-service NAME=my_service
```

脚本在 `services/my_service/` 下生成与 `property_verification` 一致的骨架，
不包含任何业务实现。随后：

1. 在 `docs/service-registry.md` 登记新服务；
2. 按需补充服务的 `pyproject.toml`（依赖声明）与 `Dockerfile`；
3. 在 `app/main.py` 的应用工厂中组装 FastAPI 应用；
4. 按 api / application / domain / infrastructure 分层填充实现。

新增服务还必须在 `services/catalog.yaml` 声明 owner、maturity、api_prefix、health_path、
config_prefix、data_owner 和 dependencies。提交前至少验证健康检查、独立测试与 Docker 构建。

## 分层约定

- HTTP 协议细节只允许出现在 `api` 层；
- 用例编排放 `application`，领域规则放 `domain`；
- 一切外部依赖（数据库、OCR、HTTP 渠道、存储、队列）都通过
  `domain/providers`、`domain/repositories` 的抽象接口访问，由 `infrastructure` 实现；
- 跨服务共享代码必须先沉淀到 `packages/`，且不得包含业务逻辑。
- 不为形式完整而保留空抽象；只有存在业务规则、替换实现或测试隔离需求时才分层。
- 服务不得直接访问另一个服务的数据库、存储卷或内部 Python 模块。

## 配置约定

- 所有配置从环境变量读取，服务级示例写入各自的 `.env.example`；
- **禁止**在代码与示例中写入真实网址、账号、密码、Cookie、Token。

## 测试约定

- 单元测试放 `tests/unit`，集成测试放 `tests/integration`；
- 测试素材放 `tests/fixtures`，**只允许合成样例**，禁止真实证件图片与个人信息；
- 跨服务契约测试放仓库根 `tests/contract`，端到端测试放 `tests/end_to_end`。
- 所有受保护资源必须覆盖至少一个允许用例和一个越权拒绝用例；上传接口需覆盖类型、大小和路径安全。

## 数据与身份约定

- 每个服务独占自己的表和文件；跨服务一致性优先使用幂等事件和补偿，而非跨库事务；
- 第二个服务需要身份认证时，不复制用户表，应按 ADR-0001 提取统一身份服务或对接企业 SSO；
- 网关只能执行通用认证，资源范围授权由业务服务执行；
- 表结构进入生产后必须使用迁移工具维护，禁止启动时执行破坏性变更。

## 占位约定

未实现的方法统一使用 `raise NotImplementedError`，并附 `TODO` 说明；
禁止在结构阶段提前实现下一阶段功能。
