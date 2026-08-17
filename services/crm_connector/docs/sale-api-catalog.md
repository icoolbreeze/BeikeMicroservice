# 买卖 API 分类目录

本目录按 CRM 业务板块归类，而不是按上游 URL 或前端页面文件归类。买卖（`property.sale`）
对应上游 `house.link.lianjia.com` 工作台，2026-08-11 用真实扫码凭据在
`/search/sale/default/gdiv_mt` 页面逐项抓包核验。

```text
CRM
└─ 房源 (property)
   ├─ 租赁 (property.rental)     已实现
   └─ 买卖 (property.sale)       已实现
      ├─ 买卖房源（全部房源） (property.sale.listing_search)  已实现
      ├─ 买卖房源详情 (property.sale.listing_detail)         已实现
      └─ 买卖地图找房 (property.sale.map_search)             已实现
```

## 认证域说明

买卖业务 API 挂在 `house.link.lianjia.com`（shiro-cas 域），与租赁的
`lease-pz.link.lianjia.com` 不同。已核验（2026-08-11）：

- 仅带租赁材料（`puzu_lease_token` + `saas_token`）调用 `/search/searchQueryNew`
  返回 `403 未登录认证`；
- 补上 shiro-cas 跳转种下的 `HOUSEJSESSIONID` 后返回 `code:1` 正常数据；
- 因此 `KeComQrBootstrapProvider` 的 `_BUSINESS_COOKIE_NAMES` 与
  `KecomSessionProvider` 的 `_COMPAT_COOKIES` 均已加入 `HOUSEJSESSIONID`，
  扫码/TGC 刷新后材料自带该 cookie；
- 请求头签名（与浏览器一致）：`lianjia_curworkcity=510100`、
  `lianjia_bucid=<UCID>`、`x-requested-with: XMLHttpRequest`、
  `referer: https://house.link.lianjia.com/search/sale/default/gdiv_mt`。
- 成功信封码是 `code:1`（非租赁域的 `100000`）。

## 已实现：买卖房源（全部房源）筛选

模块 ID：`property.sale.listing_search`

### 搜索：`GET /search/searchQueryNew`

固定参数（与浏览器逐字一致，连接器在受控路由内补齐）：

```
alertContent= alertTitle= algorithmPunishType=0 buttonVoList=
currentPage=<page> del_type=1 evtId= level=0 maskAllHouse=false
punish=false punishCode=500100000004 riskLabelAction=0 riskLabelPerson=1
riskProtectMainHouse=0 riskStrategy= riskStrategyInfo= season=
sort=<sort> tabSort=default timeLocal= ucid= vertical=<scope>
```

分页：上游**固定 30 行/页**，`pageSize` 参数实测被忽略（2026-08-16 请求
`pageSize=5` 仍返回 30 行）；连接器因此不暴露 `page_size`，翻页仅用 `currentPage`。

业务筛选参数（仅当用户指定时发送；多值组用逗号拼接，与页面一致）：

| 参数 | 含义 | 取值来源 |
| --- | --- | --- |
| `vertical` | 范围（维护盘/共享盘/积分盘/店共享池/店东共享池/维护盘共享池/关注/角色） | `getSearchFilters` vertical 组 id |
| `disId` | 商圈 | disId 组 id（如 `510108` 成华） |
| `price` | 总价区间（万元），`lo,hi`，hi=-1 表示以上 | price 组 id 或自定义 |
| `area` | 面积区间（平米），`lo,hi` | area 组 id 或自定义 |
| `room` | 房型，`lo,hi`（如 `2,2` 两室、`5,-1` 五室以上） | room 组 id |
| `floorNew` | 楼层：`under_ground`/`first_floor`/`top_floor`/`not_*` | floorNew 组 id |
| `orient` | 朝向编码，逗号拼接（`100500000003;100500000007` 南北组合） | orient 组 id |
| `houseLayout` | 户型：`withTerrace`/`withCourtyard`/`withAttic`/`brightBathroom`/`northSouthTransparent`/`bedroomFacesSouth` | houseLayout 组 id |
| `tag` | 标签：`has_subway`/`mwwy` 满五唯一/`mw` 满五/`me` 满二/`bikan_haofang`/`rent_sale`/`vrStatus`/`is_school`/`buy_limit`/`is_elevator_house`/`new_house_in_7_days`… | tag 组 id |
| `h_age` | 建成年代：0 两年内 … 6 三十年以上 | h_age 组 id |
| `visitable_times` | 可看：1 今天 … 4 随时 | visitable_times 组 id |
| `payment_mode` | 交易权属（商品房 `307500000001` 等） | payment_mode 组 id |
| `b_type` | 建筑类型（塔楼 `102200000001`、板楼 `102200000002`…） | b_type 组 id |
| `appro_broker` | 实勘：1 有 / 0 无 | select 组 |
| `key_broker` | 钥匙：1 有 / 0 无 | select 组 |
| `hasSmartLock` | 智能门锁：1/0 | select 组 |
| `role` | 我的角色：0 录入 / 1 维护 / 2 钥匙 / 3 实勘 / 1011121314 备件 | select 组 |
| `house_stat` | 房屋现状：001 自住 / 002 空置 / 003 出租中 / 004 经营中 | select 组 |
| `credential_type` | 证件状态：0 不全 / 1 齐全 | select 组 |
| `isParkingPlace` | 有无车位：1/0 | select 组 |
| `statFunction` | 房屋用途（普通住宅 `107500000003` 等） | select 组 |
| `del_grade` | 房屋等级：A/B/C | select 组 |
| `fitment_status` | 装修：003 精装 / 001 简装 / 002 毛坯 | select 组 |
| `bathroom_n` | 卫生间数：`1,1` … `6,-1` 5卫以上 | select 组 |
| `appearance` | 外网呈现：1 已呈现 / 0 未呈现 | select 组 |
| `houseSpread` | 推广：`ddttg` 点点通 / `jjfy` 聚焦 / `tgjxs` 推广金悬赏 | select 组 |
| `isElevatorHouse` | 电梯：1/0 | select 组 |
| `frameStructureFilter` | 户型结构：0 平层 / 1 跃层 / 2 复式 / 3 错层 / 4 loft / 5 跃复一体 | select 组 |
| `beautifyHouse` | 美化房：`basicBeautifyHouse` 等 | select 组 |
| `haveOwnerReservePrice` | 业主预期价：0 未填写 / 1 已填写 | select 组 |
| `multi_community_id` | 精确小区（逗号分隔多个） | `sugCommunityInfo` |
| `del_code` | 房源编号精确查询 | 用户提供 |
| `sort` | `period1_desc_createtime_desc`（默认新上优先）/ `period1_asc_totalprice` / `period1_desc_totalprice` | 页面表头 |

响应信封：`{code:1, data:{totalCount, totalPage, currentPage, list:[…]}}`，每页 30 条。
`has_more` 由 `currentPage < totalPage` 推导。行内关键字段：

`houseDelCode`（房源编号）、`communityName`、`bizCircleName`、
`unitType`（如 `2-1-1-1`）、`areaSize`、`totalPrice`（元）/`totalPriceStr`（如 `60万`）、
`unitPrice`（元/平）、`floorType`（低/中/高）+`totalFloor`（组成 `高/7` 楼层文案）、
`orientation`、`tags`（满五唯一/钥匙/VR房…）、`visitCount`（15天带看）、`followUp`、
`createTime`（毫秒）、`maintainerName`/`maintainerTag`/`maintainPercentage`（维护完成度）、
`qualityScore`（评分，字符串数字）、`holderLevel`（S/A/B…）、`delType`、
`communityId`、`paymentMode`、`statFunction`、`subwayLineName`/`subwayName`、
`vrStatus`、`surfaceImage`（封面图原图）、`floorPlanImage`（户型图原图）。

### 筛选字典：`GET /search/getSearchFilters?del_type=1&searchTab=ALL_TAB`

返回 14 个条件组（2026-08-11 实测）：`vertical`（范围 9 项）、`disId`（商圈 25 项）、
`price`（价格 10 档）、`area`（面积 9 档）、`room`（房型 7 档）、`floorNew`（楼层 7 项）、
`orient`（朝向 10 项）、`houseLayout`（户型 6 项）、`tag`（标签 19 项）、
`select`（筛选 dropdown 17 个）、`h_age`（建成年代 7 档）、`visitable_times`（可看 4 档）、
`payment_mode`（交易权属 13 项）、`b_type`（建筑类型 4 项）。

每个组 `{name, id, key, type(radio/checkbox), defaultValue, forShow, ext, children}`，
叶子 `{id, name}`。`select` 组子节点自带 `key` 与二级 children（即 17 个 dropdown 的
key=value 枚举）。价格/面积/房型/年代组的 `ext` 含 `{min,max,type:"range",unit}`，
支持自定义区间。

### 小区联想：`GET /search/sugCommunityInfo?delType=1&q=<关键词>`

返回 `data:[{text, districtName, bizcircleName, resblockName, resblockAlias,
communityId, delType}]`。`communityId` 直接回填 `searchQueryNew.multi_community_id`
实现精确单/多小区过滤；不能用联想词当模糊查询。

## 已实现：买卖房源详情

模块 ID：`property.sale.listing_detail`

| MCP 工具 | 上游 | 用途 |
| --- | --- | --- |
| `sale_listing_get_detail` | `searchQueryNew?del_code=<id>` | 列表行（与搜索同构，取首行） |
| `sale_listing_get_detail_head` | `housedel/views` + `housedel/housedelExtInfo` | 详情头 + 小区/楼栋属性 + 外网呈现 |
| `sale_listing_get_maintain_info` | `housedel/getMaintainInfo` | 维护信息（看房/价格/业主/特色） |
| `sale_listing_get_follows` | `housedelfollow/queryfollows` | 跟进记录（含钥匙/看房/交易进度） |

`housedel/views?housedelCode=<id>` 的 `data.housedelBaseInfo` 是详情头：
`displayName`/`displayPrice`/`latestPrice`/`unitPrice`/`area`/
`bedroomAmount`/`parlorAmount`/`toiletAmount`/`cookroomAmount`/`displayFloor`/
`orientation`/`delGrade`/`brokerGrade`/`holderInfo{name,orgName}`/`lastDays`/
`ctime`/`houseOrigin`/`houseId`/`acnHouseId`/`resblockId`/`resBlockInfo`/
`vrStatus`/`ownerReservePrice`/`inventoryScore`/`housedelStatus`/
`isCredentialCompleted`。

`data.basicInfo` 是小区/楼栋属性：`districtName`/`bizcircleName`/`buildYear`/
`buildType`/`buildStruct`/`dealProp`/`houseUsage`/`tenementFee`/`heatFee`/`gasFee`/
`waterType`/`eletricType`/`heatType`/`hasGas`/`hasHotWater`/`hasMidWater`/
`midWaterFee`/`hotWaterFee`/`carRatio`/`carOnground`/`carUnderground`/`parkFee`/
`hasLift`/`liftHouseRatio`/`schoolInfo`/`propYears`/`buildingDisgust`。

`housedel/housedelExtInfo` 返回外网呈现：`lianjiaUrl`/`beikeUrl`/`vrUrl`/
`netWorkStatus`。

`housedel/getMaintainInfo` 的 `data.maintainBasicInfo.maintainList` 是分组维护信息
（看房信息/价格信息/业主信息/特色信息），字段 `{key,name,value,important,comment}`，
`value` 为页面渲染文案（如 `房屋现状=出租中`、`抵押情况=有抵押，业主自还`、
`是否满N=房本日期，满二`）；`data.importantBasicInfo` 是重点字段摘要与
`completeRate`（如 `9/9`）和 `lastUpdateTime`。

`housedelfollow/queryfollows?type=0&currentPage=1&housedelCode=<id>&pageSize=100`
返回跟进记录 `{id, followContent, creatorName(含角色/门店/电话),
createTime(文案如 2026-08-07 16:58), onTop, remarks, followLabel, videoUrl}`。

## 与租赁的差异速查

| 维度 | 租赁（lease-pz） | 买卖（house.link） |
| --- | --- | --- |
| 认证 cookie | `puzu_lease_token` 族 | `saas_token` + `HOUSEJSESSIONID` |
| 成功信封 | `code:100000` | `code:1` |
| 成功码语义 | `100001` 无效入参 | `403` 未登录 |
| 搜索路由 | `/api/houseList/search/pc/list` | `/search/searchQueryNew` |
| 筛选字典 | `/api/houseList/search/pc/searchOption` | `/search/getSearchFilters` |
| 小区联想 | 地图域 `sug`（itemType 分型） | `/search/sugCommunityInfo`（communityId） |
| 详情 | `detailHead` 等 7 个路由 | `housedel/views` + ext/maintain/follows |
| 范围 | `relationRange` 1/4/9 | `vertical` 9 个池 id |
| 地图域 | 代理 `map.ke.com` | house.link 自身 `/search/map/*` |

## 已实现：买卖地图找房（附近范围查询）

模块 ID：`property.sale.map_search`

与租赁的 nearby 同构，不依赖浏览器画圈交互：解析地点坐标 → 按半径计算外接
bounds → 加载范围内小区气泡 → Haversine 过滤半径内小区 → 小区 id 回灌列表搜索
（`multi_community_id`）。是小区质心近似，不是房产坐标距离保证。

| MCP 工具 | 上游 | 用途 |
| --- | --- | --- |
| `sale_map_suggest` | `GET /search/map/suggest?deltype=1&city_id=<id>&query=<词>` | 地点解析为带坐标的小区条目 |
| `sale_map_nearby_search` | suggest + `bubbleSearch` + `searchQueryNew` | 半径内小区质心过滤后查询在售房源 |

### 地点联想：`GET /search/map/suggest`

返回 `data:[{id, text, alias, bizcircleName, districtName, type, count,
latitude, longitude, unitPrice}]`。当前观测 `type` 恒为 `community`；商圈/地标
（万象城→华润广场(成华)、东郊记忆→东郊记忆·音乐公园）会被解析成带坐标的小区条目，
首个匹配通常即目标。`latitude`/`longitude` 为字符串坐标（BD-09）。

### 气泡：`GET /search/map/bubbleSearch`

参数（与浏览器一致）：`deltype=1`、`city_id`、`group_type`（`district`/`community`）、
`max_lat`/`min_lat`/`max_lng`/`min_lng`（外接矩形）、`filters`（JSON 字符串，空对象
为 `{}`）及与列表页同源的固定审计参数。

返回 `{code:1, data:{total_count, visible_count, list:{<id>: {id, unit_price,
name, count, latitude, longitude, border, desc}}}}`；`list` 是按 id 索引的对象。
`group_type=community` 时 `id` 即小区 id，可直接回灌
`searchQueryNew.multi_community_id`（已验证：`multi_community_id=1611043057386`
在 `vertical=all` 下返回 28 套）。

### 附近查询链路（`sale_map_nearby_search`）

1. `sale_map_suggest(location)` → 取首个带坐标的条目作圆心（用户也可直接传
   `center_latitude`/`center_longitude`）；
2. `_radius_bounds` 计算半径外接矩形 → `bubbleSearch(group_type=community)`；
3. Haversine 过滤 `distance <= radius_meters` 的小区（去重、最多返回 100 个）；
4. 小区 id 填入 `searchSaleListings` 的 `community_ids`（
   `multi_community_id`）返回在售房源页。响应中的 `matched_community_count` 是半径内全部
   小区数；超过 100 个时设置 `community_ids_truncated=true`，房源结果只覆盖返回的小区。

实测（2026-08-11）：`万象城` 半径 1000m → 44 个小区 → `vertical=all` 下 2003 套在售
（华润二十四城五期 102万、蜜城(双林南支路) 89万…）；叠加 `total_price_wan 50..150`
+ `rooms [2,3]` → 437 套。

**scope 语义**：nearby 默认 `all`（不限）——半径圈天然跨池，`gdiv_mt`（维护盘）下
同参数常返回 0（该经纪人不维护圈内小区）；仅当用户明确限定自己池子时才传具体 scope。
列表页 `sale_listing_search` 保持 `gdiv_mt` 默认不变。

空结果规范：`searchQueryNew` 在 `multi_community_id` 无可见房源时返回
`totalCount=0, list=null`（非错误），连接器映射为空页（2026-08-11 实测）。
