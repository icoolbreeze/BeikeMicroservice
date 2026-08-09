# 租赁地图找房 MCP

无浏览器 CRM Connector 只暴露两项地图能力：

- `rental_map_suggest`：地点、小区或商圈联想。
- `rental_map_nearby_search`：地点 + 圆形半径 + 预算 + 户型的一步查询。

可视地图范围、气泡层级和用户手工画圈均依赖浏览器地图交互，因此不暴露为 MCP 或 HTTP API。
连接器内部仍使用气泡和画圈房源上游路由实现“附近”查询；调用方无需也不能传入视窗 bounds、
地图 zoom 或多边形坐标。

所有上游请求均必须经过 `SessionProvider.authorized_fetch` 的路由白名单；MCP 不接收、返回或记录登录凭据。

## 附近搜索语义

`rental_map_nearby_search` 执行以下内部步骤：

1. 通过地点联想解析圆心。
2. 根据半径生成候选范围并加载社区气泡。
3. 以 Haversine 距离筛选社区中心点位于圆内的社区，最多保留 200 个。
4. 以这些社区 ID 查询房源。

因此结果是 **社区中心点半径** 检索，不是房源坐标的精确圆形检索。响应的
`approximation` 固定为 `community_centroid`，并返回匹配社区数。

示例：

```json
{
  "input": {
    "location": "万象城",
    "radius_meters": 1000,
    "price_min_yuan": 1000,
    "price_max_yuan": 2500,
    "rooms": [2]
  }
}
```

房型使用 `rooms: [1..5]`，其中 `5` 表示五室及以上；租赁方式使用
`rental_modes` 的 `whole_rent` 或 `shared_rent`。合租默认不设置最低价，除非客户明确提出。

如调用方已通过可信地理编码服务得到坐标，可同时传入 `center_latitude` 与
`center_longitude`；两个字段必须成对提供，并使用同一坐标系。
