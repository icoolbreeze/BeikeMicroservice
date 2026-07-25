# scripts

平台开发脚本目录。

## create_service.py

创建新的微服务骨架（目录与占位文件，不包含任何业务实现）：

```bash
python scripts/create_service.py my_service
# 或使用 Makefile
make new-service NAME=my_service
```

生成结果位于 `services/<service_name>/`，分层约定与 `services/property_verification` 一致。
创建后请在 `docs/service-registry.md` 中登记新服务。
