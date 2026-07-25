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

## 分层约定

- HTTP 协议细节只允许出现在 `api` 层；
- 用例编排放 `application`，领域规则放 `domain`；
- 一切外部依赖（数据库、OCR、HTTP 渠道、存储、队列）都通过
  `domain/providers`、`domain/repositories` 的抽象接口访问，由 `infrastructure` 实现；
- 跨服务共享代码必须先沉淀到 `packages/`，且不得包含业务逻辑。

## 配置约定

- 所有配置从环境变量读取，服务级示例写入各自的 `.env.example`；
- **禁止**在代码与示例中写入真实网址、账号、密码、Cookie、Token。

## 测试约定

- 单元测试放 `tests/unit`，集成测试放 `tests/integration`；
- 测试素材放 `tests/fixtures`，**只允许合成样例**，禁止真实证件图片与个人信息；
- 跨服务契约测试放仓库根 `tests/contract`，端到端测试放 `tests/end_to_end`。

## 占位约定

未实现的方法统一使用 `raise NotImplementedError`，并附 `TODO` 说明；
禁止在结构阶段提前实现下一阶段功能。
