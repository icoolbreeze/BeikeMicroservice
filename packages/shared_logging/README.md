# shared_logging

公共日志包：统一日志格式与日志脱敏。

## 规则

- 只提供日志初始化与脱敏工具；
- 各服务通过 `get_logger` 获取 logger，不自行配置格式。
