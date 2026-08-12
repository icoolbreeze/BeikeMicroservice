---
name: sale-search
description: Search CRM 买卖 (sale/resale) listings with exact community resolution, rich filters (price/area/rooms/floor/orientation/layout/tags/house-age/visitable/payment-mode/building-type plus the 筛选 dropdowns), radius searches around a named place (万象城附近), and detail lookups (detail head with 小区/楼栋 attributes, maintain info, follow-up records). Use when a user asks to find homes for sale by community, budget, room count, 满五唯一/地铁房/电梯房 tags, nearby radius, or other 在售 criteria, or to inspect a specific 买卖 listing's detail-page information.
---

# Sale (买卖) search

Use the CRM MCP tools. Use the browser only when the user explicitly asks to inspect or verify the CRM UI.

> **开发副本**：本目录是开发/调试副本（`skills/sale-search`）。部署时把整个 `sale-search` 目录复制到目标 agent 的 skill 目录（如 `~/.codex/skills/`、`~/.claude/skills/`）后再修改线上版本；本副本保持与线上一致以便对照调试。

## Choose the search path

| User intent | Use |
| --- | --- |
| Names one or more communities | `sale_community_suggest` then `sale_listing_search.community_ids` |
| Says nearby / within a radius of a landmark or mall | `sale_map_nearby_search` |
| Needs rich filters without an exact community | `sale_listing_search` |
| Asks about a specific 房源编号 | `sale_listing_get_detail` |

## Nearby-radius workflow

Use `sale_map_nearby_search` for phrases such as “万象城附近 1 公里” or “东郊记忆附近”. It resolves the center, loads community bubbles, and includes communities whose centroids are within the requested Haversine radius. It is a community-centroid approximation, not a property-coordinate distance guarantee.

**The user's stated criteria are the complete filter set — never add filters of your own.** “附近” means radius-only: send `location` + `radius_meters` plus exactly what the user asked for (price, rooms, etc.).

- `location` accepts communities, malls, landmarks, and roads — the connector's sale map suggest resolves all of them to a coordinate-bearing community entry (万象城 → 华润广场(成华)). The response's `center.text` states what was actually resolved; report it so the user sees their mall became a community.
- Report `approximation`, the matched-community count, and the center resolution in every answer.
- **scope 语义**：`sale_map_nearby_search` 默认 `scope=all`（不限）— a radius circle legitimately crosses pool boundaries, and pool-scoped queries (gdiv_mt 维护盘) often return 0 for a circle the agent does not maintain. Only pass `gdiv_mt`/`gdiv_share`/… when the user explicitly restricts to their own pools. `sale_listing_search` keeps `gdiv_mt` as its default (workbench default), so the two tools differ here on purpose.
- “N 万元左右” on a nearby search becomes an explicit price range `total_price_wan` (e.g. 100万左右 → 50..150). Preserve an explicitly stated range instead.
- “套二 / 两居” → `rooms: [2]`; “两到三居” → `rooms: [2, 3]`.
- When the user says “附近” without a distance, use the API's default radius (1000 m) and state that radius in the answer.
- If additional rich filters are requested on top of a nearby search (tags, 满五唯一, 实勘, 电梯…), apply them **after** the circle: fetch the circle's listings, then filter the returned items client-side by tags/price/户型; or pass the circle's `community_ids` into `sale_listing_search` with the catalog filters (see Map ↔ 全部房源 联动).
- Use a circle/radius, never a rectangular approximation, when the user states a nearby distance.

## Map ↔ 全部房源 联动（map-to-list cooperation）

The map flow (suggest / nearby) answers “where”; the list flow (`sale_listing_search`) owns the full filter catalog. When a query needs both — a location and rich filters — combine them:

| Scenario | Combine how | Notes |
| --- | --- | --- |
| User names community(ies) + rich filters | `sale_community_suggest` → `community_ids` → `sale_listing_search` with the user's filters | Full catalog available; preferred path (see Exact community workflow) |
| Nearby + catalog-only filters (满五唯一, 实勘, 电梯, 外网呈现…) | Take the circle's `community_ids` from `sale_map_nearby_search` → pass them as `community_ids` into `sale_listing_search` with the catalog filters | **Preferred path** — one extra call, circle semantics preserved (list search only touches those communities). Note scope: pass `scope` explicitly since list default is gdiv_mt |

In every combined query, keep the map semantics in the answer: resolved center, `approximation`, matched-community count, and whether the circle or the list filter decided the result set.

## Exact community workflow

1. Call `sale_community_suggest` once for each requested community name.
2. Select the matching result and take its `community_id` — the connector's suggest is the 买卖-native resolver (returns `communityId` that maps to the page's `multi_community_id` filter). Never invent community ids.
3. Deduplicate the ids and pass them as `community_ids` to `sale_listing_search`.
4. Add the user's rich filters to that same search. Do not combine `listing_id` with `community_ids`.
5. State the resolved standard community names, any unresolved names, result count for the returned page, and whether another page is available (`has_more`).

## Filters and budget

Call `sale_listing_filter_options` before using unfamiliar keys or values. Use only the returned catalog ids.

- **总价 (price)**: 万元区间 → `total_price_wan: {"min": 50, "max": 70}` = 50-70万; `{"min": 500}` alone = 500万以上. Catalog buckets: `0,50` 50万以下 … `500,-1` 500万以上.
- **面积 (area)**: 平米区间 → `area_sqm: {"min": 70, "max": 110}`.
- **房型 (rooms)**: `rooms: [2]` = 2室, `rooms: [2, 3]` = 2-3室 (range), `rooms: [5]` = 5室以上.
- **楼层 (floors)**: `floors: ["first_floor"]` 一层, `["top_floor"]` 顶层, `["not_under_ground"]` 不看地下室, etc.
- **朝向 (orientations)**: catalog codes — 100500000001 东 … 100500000008 东北; `["100500000003;100500000007"]` = 南北.
- **户型 (house_layouts)**: `withTerrace` 带露台 / `withCourtyard` 带小院 / `withAttic` 带阁楼 / `brightBathroom` 明卫 / `northSouthTransparent` 南北通透 / `bedroomFacesSouth` 卧室朝南.
- **标签 (tags)**: `mwwy` 满五唯一 / `mw` 满五 / `me` 满二 / `has_subway` 地铁房 / `is_elevator_house` 电梯房 / `bikan_haofang` 必看好房 / `buy_limit` 不限购 / `rent_sale` 租售 / `new_house_in_7_days` 新上房源 / `is_school` 附近有学校 …
- **建成年代 (house_age)**: 0 两年内 … 6 三十年以上.
- **可看 (visitable_times)**: 1 今天可看 … 4 随时可看.
- **交易权属 (payment_mode)**: 307500000001 商品房 / 307500000002 已购公房 / 307500000016 私产 …
- **建筑类型 (building_type)**: 102200000001 塔楼 / …0002 板楼 / …0003 塔板结合 / …0004 平房.
- **筛选 dropdown (select)**: key=value, e.g. `{"appro_broker": 1}` 有实勘, `{"key_broker": 1}` 有钥匙, `{"house_stat": "002"}` 空置, `{"fitment_status": "003"}` 精装, `{"appearance": 1}` 外网已呈现, `{"haveOwnerReservePrice": 1}` 业主预期价已填写, `{"del_grade": "A"}` A级房屋. Value `-1` (不限) is dropped automatically.
- **范围 (scope)**: `gdiv_mt` 维护盘 (default) / `gdiv_share` 共享盘 / `gdiv_score_division` 积分盘 / `share_pool_org` 店共享池 / `jmgroup_pool` 店东共享池 / `acn_pool` 维护盘共享池 / `follow_housenew` 关注房源 / `rolenew` 角色房源. Honor the user's explicit scope wording when given; otherwise keep the default.
- **排序 (sort)**: `period1_desc_createtime_desc` 新上优先 (default) / `period1_asc_totalprice` 总价升序 / `period1_desc_totalprice` 总价降序.

**The user's stated criteria are the complete filter set — never add filters of your own.**

## Listing details (房源详情)

Once a listing is found, answer follow-up questions about that specific house from its detail tools:

| Tool | Covers |
| --- | --- |
| `sale_listing_get_detail` | 列表行：小区/商圈/户型/面积/总价/单价/楼层/朝向/标签/15天带看/维护人/维护完成度/评分/等级/地铁/VR |
| `sale_listing_get_detail_head` | 详情头：价格/户型/楼层/朝向/房屋等级/维护人与门店/挂牌天数/来源/业主预期价/库存分 + 小区/楼栋属性（建筑年代、类型、结构、交易权属、物业费、水电气、供暖、车位、电梯、梯户比、学区、产权年限、凶宅嫌恶）+ 外网链接 |
| `sale_listing_get_maintain_info` | 维护信息：看房信息/价格信息/业主信息/特色信息 分组字段（房屋现状、是否唯一、户口、抵押、产权共有、是否合同房、产权面积、是否满N、车位、学区名额、装修、交房）+ 重点字段 + 完备率 |
| `sale_listing_get_follows` | 跟进记录：钥匙位置、看房便利、房东心态、租客情况、交易进度等 field notes |

Use the detail tools to settle questions the search catalog cannot answer: exact 抵押/户口/产权 status from `get_maintain_info`, key/viewing logistics from `get_follows`.

**These are valid answers, not errors**: `follows: []` — no follow-ups yet; maintain fields with empty `value` — not filled in; `owner_reserve_price` empty — not recorded. A `sale_listing_get_detail` invalid-input error means the id is not visible to the caller — do not retry with another id.

## Reliability and privacy

Check connection status before a live query. If authentication is required, ask the user to scan using the connector's local login flow; never expose or persist credentials, QR contents, cookies, tokens, or account IDs.

Do not invent community ids, filter values, or search results. Report ambiguity and empty results plainly, and offer the least-assumptive relaxation only after explaining it.
