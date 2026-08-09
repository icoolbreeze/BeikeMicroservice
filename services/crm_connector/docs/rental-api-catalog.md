# 租赁 API 分类目录

本目录按照 CRM 业务板块归类，而不是按上游 URL 或前端页面文件归类。每个 MCP 工具和
`GET /api/v1/mcp/tools` 的元数据都带有对应的 `moduleId`，便于调用方按板块发现能力。

```text
CRM
└─ 房源 (property)
   └─ 租赁 (property.rental)
      ├─ 地图找房 (property.rental.map_search)     已实现
      └─ 房源列表（全部房源） (property.rental.listing_search) 已实现
```

## 已实现：地图找房

模块 ID：`property.rental.map_search`

| MCP 工具 | 用途 |
| --- | --- |
| `rental_map_suggest` | 地点、小区和商圈联想；可返回地图坐标。 |
| `rental_map_nearby_search` | 以地点/坐标为圆心、按半径筛选小区并查询房源。 |

范围找房的最终纳入规则是圆形 Haversine 距离；外接 bounds 只用于请求候选气泡。
可视地图范围、气泡和用户画圈依赖浏览器交互，不作为无浏览器 CRM Connector 的对外 API/MCP
能力暴露；它们仅保留为附近圆形查询的内部实现细节。

## 已实现：房源列表（全部房源）筛选

模块 ID：`property.rental.listing_search`

已通过页面核验：路由 `/rent/house/list` 的模块名称是“房源列表”，当前默认选中页签是
“全部房源”；不是“所有房源”。现有 `rental_listing_search` 和
`rental_listing_get_detail` 已归入此模块。筛选字典由
`rental_listing_filter_options` 从页面同源的 `searchOption` 路由读取；它包含 37 个顶级
筛选节点及其当前可用值，例如房源类型、价格、面积、房型、朝向、标签、租赁方式、付款方式、
租期、房屋现状、装修、建筑类型、房龄、交易权属和范围。

精确小区筛选需组合地图联想与房源列表：先用 `rental_map_suggest` 解析小区名称并选择
`item_type=resblock` 的结果，再将其 `item_id`（可多个）传给
`rental_listing_search.resblock_ids`。连接器按页面原生方式提交逗号分隔的 `resblockId`；
该方式可继续叠加房源列表的全部丰富筛选。`community_keyword` 仅保留为页面的模糊文本搜索，
不能替代精确小区 ID。

`rental_listing_search.condition_filters` 用于这些页面原生筛选。调用方必须先读取
`rental_listing_filter_options`，再传入其中的键和值；连接器只接受这份已核验目录中的键，
不会把 MCP 变成任意上游参数代理。已用真实 CRM 验证的组合包括：
`rentType=002`（合租）、`bedroomAmount=2`（两室）、`orientation=100500000003`（南向）和
`price=0:3000`（3000 元以下）。

预算算法：普通租赁下限 = 预算 ÷ 2；上限 = 预算 + `clamp(预算 × 25%, 200, 500)`。例如
700 元为 `350:900`，2000 元为 `1000:2500`，3000 元为 `1500:3500`。若客户明确为合租，
则不设置最低价，仅保留相应的最高价。`rental_listing_search` 可直接传入 `budget_yuan` 让
连接器生成该区间，且不能同时传入 `condition_filters.price`。

不会改变地图找房的路由或筛选语义。

## 房源详情

`rental_listing_get_detail` 直接调用页面详情路由
`GET /api/puzu/house/detail/detailHead?delCode=<id>`（2026-08-09 从真实详情页抓包确认，
不再复用房源列表搜索）。detailHead 的字段名与列表不同：`housePrice`/`houseArea`/
`livingroomAmount`/`oriented`，映射为 `monthly_rent_yuan`/`area_sqm`/`layout`/`orientation`。

ID 语义：列表与地图返回的 `delCode` 属于普租域。地图 `actionUrl` 中部分 ID（如
10611245074901）属于托管域房源，在普租域详情中不存在；detailHead 对这类 ID 返回空
`data`，连接器将其报告为“房源编码错误，房源不存在”（invalid-input 错误），绝不回退到
搜索返回的其它房源。
