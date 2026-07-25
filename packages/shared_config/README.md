# shared_config

公共配置包：环境枚举与配置加载入口的统一约定。

## 规则

- 只提供通用加载机制与公共枚举；
- 各服务的具体配置字段定义在自身 `infrastructure/config/settings.py` 中。
