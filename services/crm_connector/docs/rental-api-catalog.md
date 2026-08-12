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

列表行返回图片字段（2026-08-10 接入）：`title_image_url`（上游 `titleImage`，封面图，
实勘照片也常在此）与 `floor_plan_image_url`（上游 `floorPlanImage`，户型图）。两者均为
`img.ljcdn.com` **原图 URL，连接器刻意不加尺寸后缀**——调用方需自行拼接 CDN 尺寸后缀
（`.450x.jpg` 缩略图 → `.750x`/`.800x` → `.1500x.jpg` 最高清）后即可免凭证公开下载；
原图直连按路径而异：`lease-image` 封面桶公开 200，实勘/户型图桶 403（含登录态）。
规则见 [rental-image-cdn.md](rental-image-cdn.md)，MCP 工具描述中已内置该指引。

`rental_listing_search.condition_filters` 用于这些页面原生筛选。调用方必须先读取
`rental_listing_filter_options`，再传入其中的键和值；连接器只接受这份已核验目录中的键，
不会把 MCP 变成任意上游参数代理。已用真实 CRM 验证的组合包括：
`rentType=002`（合租）、`bedroomAmount=2`（两室）、`orientation=100500000003`（南向）、
`price=0:3000`（3000 元以下）、`label=81`（宠物友好）和 `districtId=510107`（武侯区）。
标签类条件必须走 `condition_filters.label`（如 `label=81` 宠物友好、`label=2` VR房）；
顶层不存在 `tags` 参数（上游会静默忽略）。

`scope` 映射到页面原生「范围」条件：`all`→`relationRange=0`（不限）、
`my_maintained`→`relationRange=1`（维护盘，默认）、`shared`→4（店共享池）、
`role_visible`→9（角色房源）。区县筛选使用
`condition_filters.districtId`（如 `510107` 武侯、`510113` 青白江），顶层无 `districts`
参数。

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

详情返回在列表字段之外附加 detailHead 独有的维护与经营字段（2026-08-09 实测补全，
搜索列表行这些字段均为 `null`）：

| 响应字段 | 上游字段 | 含义 |
|---|---|---|
| `maintain_org` | `orgName` | 维护门店 |
| `source` | `delResourceSub` | 房源来源（如"呼叫中心"） |
| `floor_desc` / `total_floors` | `floorDesc` / `totalFloor` | 楼层描述 / 总楼层 |
| `listed_days` | `alreadyCreateDays` | 已录入天数 |
| `house_grade` | `houseGrade` | 房源评级 |
| `follow_total` / `follow_last_7d` | `followTotal` / `followNum7Days` | 累计 / 近7天跟进次数 |
| `showing_total` / `showing_last_7d` | `showingTotal` / `showingNum7Days` | 累计 / 近7天带看次数 |
| `external_url_ke` / `external_url_lianjia` | `keUrl` / `lianJiaUrl` | 外网房源页链接 |
| `has_key` | `haveKey` | 是否已取钥匙 |
| `del_status_text` | `delStatusString` | 房源状态文本（如"有效"） |
| `house_id` | `houseId` | 内部房源 ID |

已知边界：detailHead 不含实勘照片（`titleImage` 恒为 `null`）、小区/楼栋属性、标签与
HQI 评分——这些内容来自其它详情页接口：实勘照片走 detailProspect（见下节），小区属性/
标签/HQI 走 house-info 聚合（见 §房源详细信息）。

ID 语义：列表返回的每条房源带有 `del_type`（上游 `delType`）：`2` = 普租、
`5` = 托管。detailHead 只覆盖普租域；托管房源的 ID（如 10611245074901）在详情端点
返回空 `data`，连接器将其报告为 invalid-input 错误（消息明确提示托管房源不受普租详情
端点支持），绝不回退到搜索返回的其它房源。因此对 `del_type=5` 的列表结果不应再发起
详情查询。

### 房源实勘（detailProspect）

`rental_listing_get_prospect` 直接调用页面详情实勘路由
`GET /api/puzu/house/detail/detailProspect?delCode=<id>`（2026-08-09 通过 Playwright
抓包真实详情页 57 个接口确认，并用现有凭证探测验证）。

响应模型 `ListingProspectResponse`：

| 响应字段 | 上游字段 | 含义 |
|---|---|---|
| `photos[].url` | `houseProspectImageList[].prospectPicUrl` | 照片地址 |
| `photos[].room_name` | `roomName` | 所属房间 |
| `photos[].image_type` | `imageType` | `REAL` = 实勘图，`TITLE` = 标题图 |
| `photos[].upload_user` | `uploadUserName` | 上传人 |
| `photos[].created_at` | `createTime`（毫秒时间戳） | 上传时间（UTC） |
| `floor_plan_url` | `houseFrameImageResp.imageUrl` | 户型图 |
| `can_edit` | `canEditProspect` | 当前员工是否有编辑权限 |
| `has_survey_photo` | 推导 | `photos` 中至少一张 `REAL` |

语义约定（2026-08-09 实测确认）：

- **未实勘是合法答案而非错误**：普租房源没有实勘照片时，
  `houseProspectImageList` 为空数组但 `data` 对象仍非空（实测 106128814453），
  此时返回 `has_survey_photo=false` 与空 `photos`。
- 只有 `data` 缺失或为空（如托管房源 ID）才报 invalid-input 错误，与 detailHead 一致。
- 实勘状态以 detailProspect 为准，不再依赖列表行的 `titleImage` 间接推断。

detailProspect 返回的其它键（`houseProspectRoomList`、`huXingPicUrl`、`titleImage`、
`uploadProspectUrl`、`vrJsonResp`）暂未建模到响应中。

**图片访问规则见 [rental-image-cdn.md](rental-image-cdn.md)**（2026-08-10 实测）：
URL 获取需凭证（本接口已覆盖），但 API 返回的 `img.ljcdn.com` 原图 URL 对任何请求
（含登录态）都是 403，必须拼接 CDN 尺寸后缀（`.450x`/`.750x`/`.1500x`/`.jpg`）才能
公开下载；公开变体带平台水印，无水印原图无公开通道。

### 房源详细信息（house-info 聚合）

`rental_listing_get_house_info` 聚合浏览器详情页「房源信息」区的五个接口（均通过
Playwright 抓包 57 个详情页接口确认，2026-08-09）：

| 上游接口 | 聚合字段 |
|---|---|
| `GET /api/puzu/house/detail/getHouseLabel?delCode=<id>` | `labels[]`（电梯房/VR房/钥/学区房…） |
| `GET /api/puzu/house/detail/detailHdicInfo?delCode=<id>` | `property_info`（基础信息，页面分三组，DOM 核实 2026-08-09）：**小区信息**（小区/所在城区/所属商圈/物业费/小区幼儿园）、**建筑信息**（建筑类型/结构/年代/房屋用途/交易权属/产权年代/凶宅信息/嫌恶设施）、**生活信息**（电梯/梯户比/用水用电类型/供暖类型+费用/燃气+费/热水+费/中水+费/车位比例+停车服务费+地上地下车位数/绿化率/容积率） |
| `GET /api/puzu/house/detail/detailHqiTab?isApp=false&delCode=<id>` | `hqi`：总分、等级、下一等级、商圈排名、待优化项数、热度指标（本房/商圈热度、访问量、商机量）、AI 优化建议 |
| `GET /api/puzuHouse/puzu/house/detail/app/getMaintainInfo?delCode=<id>` | `maintain`（维护信息）：分组字段模块（重要字段 + 其余字段），每项带上游渲染好的 `display_value`（装修情况=精装、租期=2年以内、可入住=随时入住、家具/家电明细…）；另有 `remark` 备注、整体/重点完备率、业主底价 |
| `GET /api/puzu/house/detail/detailFollow?delCode=<id>&pageSize=100` | `follows[]`（跟进记录，完整历史至多 100 条）：`content` 跟进描述（**房屋最新状态的现场笔记**，50 套房源实证 2026-08-09：看房便利/钥匙——密码锁或实体钥匙、钥匙所在门店、随时可看 vs 需提前通知、可否马上看房（云管家模板 "您的钥匙类型为:实体钥匙 / 您是否愿意交钥匙/密码给贝壳，快速带看:是 / 预计可交钥匙/密码时间:…"）；价格筹码——业主底价/佣金让步/杂费明细（水电气物业网费）；出租状态——在租 vs 续租/空置/转租/预计空出、租带卖；限制与风险——暂不推荐原因、租客限制；联系记录——回访结果/电话接通拒接关机）、`follow_type`（普通跟进/录音跟进）、`creator_name`、`role`、`created_at`、`labels`/`label_code` 标签（真实在租… 在租真实性信号）、`remarks` 独立备注、`on_top`/`on_top_time` 置顶标记（重点跟进） |

参数坑（2026-08-09 实测踩过）：`detailHqiTab` 必须带 `isApp=false`，否则上游返回
`code=100001 缺少必要的入参`——浏览器就是这么调的，抓包时注意保留完整 query 而不仅是路径。
`getMaintainInfo`/`detailFollow` 实测纯 `delCode` 即可，不需要 `isApp` 或 city header。

语义约定（2026-08-09 实测确认）：

- **HQI 允许缺失**：`detailHqiTab` 返回空 `data` 是"暂无评分"的合法答案（实测
  106128807039 返回 `{}`），映射为 `hqi: null`，不视为错误。
- **hdicInfo / getMaintainInfo 空 data 视为无效 ID**：与 detailHead/detailProspect
  一致，未知 id 返回 `code=100001 房源编码错误`（实测探测），报 invalid-input，
  绝不静默。
- **标签允许为空**：`getHouseLabel` 空数组映射为空 `labels`。
- **跟进允许为空**：`detailFollow` 无跟进时 `totalCount=0` 且 `result=None`
  （实测 106128807039），映射为空 `follows` 列表——合法答案，不是错误。
  请求带 `pageSize=100`，一次返回完整跟进历史（实测单套房源最多 82 条，
  默认 8 条与 50 条都会被截断）。
- **跟进的作用**（50 套万象城周边房源、727 条记录实证，2026-08-09）：
  跟进是维护人的现场笔记，回答"这套房子现在什么情况、能不能带看、怎么带看"。
  内容实证分类：
  1. **看房便利/钥匙**（出现频率最高）：钥匙/密码形态、钥匙存放门店
     （"随时可看，钥匙在Wy"、"钥匙在紫东一号门链家"）、随时可看 vs 需提前
     通知、云管家承诺模板（钥匙类型/可否快速带看/预计交钥匙时间）。
  2. **价格筹码**：业主底价、佣金让步（"出一半佣金，客户优质就行"）、
     杂费明细（"1200包水气物业费"、"杂费200一个月含水电物业网费"）。
  3. **出租状态**："租客续租了，看房提前联系"、"短租出去 七月份可以空出来"、
     "房东还是考虑卖"（租带卖）。
  4. **限制与风险**："暂不推荐，房东有事情未处理"、"转租，只要女性，不养宠物"。
  5. **联系记录**："回访结果：" 模板、电话接通/拒接/关机（follow_type=录音跟进）。
  标签几乎全是"真实在租"（619/727），是在租真实性信号而非内容标签；
  `on_top=true` 标记置顶的重点跟进（133/727≈18%）；`remarks` 独立备注栏
  在样本中均为空（保留字段）。托管房源（del_type=5，ID 以 501 结尾）在
  detailFollow 返回空 result，与 detailHead 一致不适用。
- **维护信息字段直接透传渲染值**：`getMaintainInfo` 的
  `importantModules`/`otherModules[].fields[].displayValue` 是上游渲染好的展示文本
  （"精装"/"随时入住"/"床/衣柜/桌椅…"），连接器原样返回，不做二次映射；
  `complete=false` 的字段值为 `--` 占位。
- `buildingYear=0` 是上游"年代未知"的哨兵值，映射为 `null`；`parkingFee` 为字符串
  （如 "350"）。

已知缺口（靠接口验证后仍未接入）：`getComment`（装修描述/户型介绍/房源亮点/小区介绍/
周边配套/交通出行）——实测两套角色房源 `commentList` 均为 `null`，非空结构无法验证，
故不接入；`detailDaiKan`（带看记录）属经营记录而非房源信息，未接入。
