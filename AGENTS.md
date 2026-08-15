# 项目规则

## crm_connector MCP ↔ Hermes 技能联动

- 房源技能的**唯一来源**是 Hermes 技能目录（`~/AppData/Local/hermes/skills/`）。仓库内不得保留技能副本（已清理）。
- **当 `services/crm_connector` 的 MCP 服务有更改时，必须检查 Hermes 侧对应技能是否需要同步修改**：
  - 工具集变更（新增/删除/重命名 MCP 工具）→ 检查 rental-search / sale-search / listing-screening 等技能的调用约定；
  - input/output schema 或默认值变更（如 `scope` 默认值、过滤目录、分页上限）→ 检查技能的查询纪律与筛选规则；
  - 上游接口行为变更 → 检查技能中"实测验证"类说明（CDN 后缀规则、cell_code 解析、中心点解析顺序等）；
  - 完成代码修改后，提醒用户在 Hermes 技能目录中核对并同步对应技能。
- 同理适用于 `services/property_verification`（pv-mcp）与其对应技能（pv-verify）。
