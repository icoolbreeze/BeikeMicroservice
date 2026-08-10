---
name: rental-search
description: Search CRM rental inventory with map-based community resolution, exact one-or-more-community filtering, nearby circle searches, and rich listing filters; inspect any listing's details (basic 小区/建筑/生活 info, maintain info, follow-up records, survey photos, HQI score). Use when a user asks to find rental homes by a community, landmark, nearby radius, budget, room count, rental mode, pet policy, or other rental-listing criteria, or to look up the full detail-page information of a specific rental.
---

# Rental search

Use the CRM MCP tools. Use the browser only when the user explicitly asks to inspect or verify the CRM UI.

> **开发副本**：本目录是开发/调试副本（`skills/rental-search`）。部署时把整个 `rental-search` 目录复制到目标 agent 的 skill 目录（如 `~/.codex/skills/`、`~/.claude/skills/`）后再修改线上版本；本副本保持与线上一致以便对照调试。

## Choose the search path

| User intent | Use |
| --- | --- |
| Names one or more communities | `rental_map_suggest` then `rental_listing_search.resblock_ids` |
| Says nearby / within a radius of a landmark | `rental_map_nearby_search` |
| Needs rich filters without an exact community | `rental_listing_search` |

Never treat `community_keyword` as an exact-community filter. It is only a fuzzy text fallback. Do not put a map `item_id` into that field.

## Exact community workflow

1. Call `rental_map_suggest` once for each requested community name.
2. Select only a `resblock` result — **this path is for when the user names a community**. If the user named a mall, landmark, or road, use the nearby flow directly with the name as `location` instead (see Nearby-circle workflow); do not force their name onto the `resblock_ids` path. Prefer a normalized exact-name match; if several plausible communities remain, ask the user to choose using the returned district/business-circle context.
3. Deduplicate the selected `item_id` values and pass them as `resblock_ids` to `rental_listing_search`.
4. Add the user's rich filters to that same listing search. Do not send both `resblock_ids` and `community_keyword`.
5. State the resolved standard community names, any unresolved names, result count for the returned page, and whether another page is available.

For example, resolve `紫东阳光` first. After choosing the `resblock` result named `成发紫东阳光`, search with:

```json
{
  "resblock_ids": ["<resolved item_id>"],
  "budget_yuan": 2000,
  "condition_filters": {"bedroomAmount": 2}
}
```

## Nearby-circle workflow

Use `rental_map_nearby_search` for phrases such as “万象城附近 1 公里” or “双桥子南一街附近”. It resolves the center, loads candidate community bubbles, and includes communities whose centroids are within the requested Haversine radius. It is a community-centroid approximation, not a property-coordinate distance guarantee.

**The user's stated criteria are the complete filter set — never add filters of your own.** “附近” means radius-only: send `location` + `radius_meters` plus exactly what the user asked for (budget, rooms, etc.). Do NOT add administrative/district filters (`districtId`, `bizCircleId`, `city_id`) — a radius circle legitimately crosses district borders, and an added district filter silently drops matching communities inside the circle.

**`location` accepts any place type, not just communities.** Users name malls, roads, and landmarks as naturally as communities — the connector resolves all of them, and you should not force a `resblock` path:

- Mall / landmark (e.g. `万象城`, `东郊记忆`): suggest returns a `bizcircle` WITH coordinates and `poi` entries typically WITHOUT — the internal picker drops the coordinate-less `poi` and centers on the `bizcircle`.
- Road (e.g. `水碾河路`): suggest has no "road" item — it returns communities along the road, and the picker centers on the first one with coordinates (e.g. `水碾河路6号`).
- The response's `center.item_type` states what was actually used (`bizcircle` / `resblock` / `provided_coordinate`); report it so the user sees their mall became a business-circle center or their road became an along-road community.

- Report `approximation`, the matched-community count, and the center resolution in every answer.
**Center resolution order (verified live)**: the connector can resolve the center from `location` itself — but when it fails, fall back in this order:

1. **Baidu POI search** (`place/v2/search`, caller-side Baidu developer AK; never put the AK in the skill, the repo, or the connector config). Returns BD-09 coordinates that are same-source with the upstream map. Verified: resolves full street names that the connector's suggest cannot (e.g. `双桥子南一街`), within ~200 m of the CRM community point. Pass the coordinates through as `center_latitude`/`center_longitude` (paired — one without the other is a 422).
2. **CRM suggest fallback** — the connector first tries its internal resolution from `location`; on `400 map location could not be resolved to coordinates`, call `rental_map_suggest` yourself (try shorter/variant forms of the name when the full name returns nothing), pick a coordinate-bearing candidate that matches the user's wording, and pass its coordinates through. Always use suggest's own coordinates.

**Never use Baidu geocoding** (`geocoding/v3`) for center resolution: verified 8–48 km resolution errors on street/vernacular names — a silent wrong center is worse than the connector's explicit 400. Coordinates from other providers (e.g. Amap GCJ-02) drift hundreds of meters with no validation error; keep everything BD-09.
- If additional rich filters are requested on top of a nearby search, apply them **after** the circle: fetch the circle's listings, then filter the returned items by the requested criteria (tags, price text, room count in the title/description). `rental_map_nearby_search` does not accept `condition_filters`, but its response now carries `community_ids` — when a filter exists only in the list catalog (e.g. `宠物友好` label, `delType`), pass the circle's `community_ids` as `resblock_ids` into `rental_listing_search` (see “Map ↔ 全部房源 联动”) instead of hand-filtering.
- When the user says “附近” without a distance, use the API's default radius (currently 1000 m) and state that radius in the answer.
- “N 元左右” on a nearby search becomes an explicit price range — the same arithmetic as `budget_yuan`: `[N/2, N + clamp(25% of N, 200, 500)]`, passed as `price_min_yuan`/`price_max_yuan`. Example: “2000 块左右” → `[1000, 2500]`.
- “套二 / 两居” on a nearby search is matched client-side from the item title (`2室` in `title`); on `rental_listing_search` it is `condition_filters.bedroomAmount=2`.
- Use a circle/radius, never a rectangular approximation, when the user states a nearby distance.

## Map ↔ 全部房源 联动（map-to-list cooperation）

The map flow (suggest / nearby) answers “where”; the list flow (`rental_listing_search`) owns the full filter catalog (`condition_filters`, `budget_yuan`, `resblock_ids`). When a query needs both — a location and rich filters — combine them as follows:

| Scenario | Combine how | Notes |
| --- | --- | --- |
| User names community(ies) + rich filters | `rental_map_suggest` → `resblock_ids` → `rental_listing_search` with the user's filters | Full catalog available; this is the preferred path (see Exact community workflow) |
| Nearby + simple filters (price, pet exclusion, rooms/rooms from title text) | `rental_map_nearby_search`, then filter `result.items` client-side by `price_text`, `tags`, `title`/`description` | Cheap for simple criteria; no extra upstream call |
| Nearby + catalog filters unavailable on nearby (e.g. `宠物友好` label, `delType`) | Take the circle's `community_ids` from the nearby response → pass them as `resblock_ids` into `rental_listing_search` with the catalog filters | **Preferred path** — one extra call, circle semantics preserved (list search only touches those communities). Verified live: 43 in-circle communities + `bedroomAmount`/`label=81` returned only in-circle homes, while the same filters without the ids returned out-of-circle ones. If `community_ids` is empty (e.g. a small circle), fall back to client-side filtering |

In every combined query, keep the map semantics in the answer: resolved center, `approximation`, matched-community count, and whether the circle or the list filter decided the result set.

## Pet policy (养宠判定)

**Default assumption: a listing allows pets unless it is explicitly marked otherwise.** “宠物友好” (pet-friendly) is a positive bonus label, NOT a requirement — filtering only on it silently drops every unlabeled-but-permissive listing.

- The `label` filter catalog distinguishes:
  - `宠物友好` (value `81` under `condition_filters.label`) — explicitly pet-friendly; a strong signal, treat as a **bonus/priority** marker, not a gate.
  - No dedicated "禁养/不可养宠" label value has been observed in the current catalog — verify against `rental_listing_filter_options` each time before assuming one exists.
- Two-tier resolution:
  1. **Exclusion tier**: exclude listings whose tags/conditions explicitly forbid pets (a label such as `禁养宠物` if present in the catalog; a nearby item whose `tags` contain a no-pets marker).
  2. **Inclusion tier**: everything remaining is pet-permissible. Prefer/prioritize `宠物友好`-tagged listings when the user has no other preference, but never return zero results just because nothing carries the pet-friendly label — report the unlabeled-but-permissible matches instead.
- On nearby circles, pets are judged from each item's `tags` (same exclusion rule); the definitive pet policy comes from `rental_listing_get_house_info.maintain` (养宠态度 display value, e.g. "业主态度: 宠物友好"), since the catalog is label-based.
- State explicitly in every pet-constrained answer: which listings carry `宠物友好`, which are merely unlabeled, and that "unlabeled" means "not marked as forbidden" per this rule.

## Listing details (房源详情)

Once a listing is found, answer follow-up questions about that specific house from its detail tools — no browser needed:

| Tool | Covers |
| --- | --- |
| `rental_listing_get_detail` | Head record: price/area/layout/orientation, 维护门店, source, floor, listing age, follow/showing counts, key status, 外网 links, 房源状态 |
| `rental_listing_get_prospect` | 实勘 photos (per-room, uploader, time), floor plan, whether surveyed |
| `rental_listing_get_house_info` | Aggregated 房源信息: labels + 基础信息 (三组) + 维护信息 + 跟进记录 + HQI |

`rental_listing_get_house_info` mirrors the detail page's 房源信息 panel:

- `property_info` — 基础信息, three groups (verified against the page DOM):
  - **小区信息**: `community` / `district` / `biz_circle` / `tenement_fee` 物业费 / `kindergarten`
  - **建筑信息**: `building_type` / `building_structure` / `building_year` / `property_purpose` / `deal_property` / `age_limit` / `disgust_desc` 凶宅信息 / `haunted_desc` 嫌恶设施
  - **生活信息**: `elevator` / `ti_hu_ratio` / `water_type` / `electric_type` / `heating` + `heating_fee` / `gas` + `gas_fee` / `hot_water` + `hot_water_fee` / `middle_water` + `middle_water_fee` / `parking_ratio` / `parking_fee` 停车费 / `parking_above_ground` / `parking_underground` / `green_rate` / `cubage_rate`
- `maintain` — 维护信息 (getMaintainInfo): grouped modules whose fields carry **render-ready display values** (`装修情况=精装`, `租期=2年以内`, `可入住时间=随时入住`, 家具/家电明细…); plus `remark` 备注, `all_field_rate`/`important_rate` 完备率, `owner_lowest_price` 业主底价
- `follows` — 跟进记录 (detailFollow, full history up to 100): the keeper's **field notes on the house's latest state** — the most useful source for vetting a listing before showing it. Verified across 50 real listings (2026-08-09), the `content` text carries:
  - **看房便利/钥匙**: 密码锁/实体钥匙, 钥匙在哪个门店 (钥匙在Wy/链家水碾河店…), 随时可看 vs 需提前通知, 可否马上看房; 云管家模板 "您的钥匙类型为:实体钥匙 / 您是否愿意交钥匙/密码给贝壳，快速带看:是 / 预计可交钥匙/密码时间:…"
  - **价格筹码**: 业主底价, 佣金让步 (出一半佣金), 杂费明细 (1200包水气物业费 / 杂费200一个月含水电物业网费,空调一块一度)
  - **出租状态**: 在租 vs 续租/空置/转租/预计空出时间, 租带卖 (房东考虑卖需配合看房)
  - **限制与风险**: 暂不推荐原因 (房东有事/卖房意愿大), 租客限制 (转租只限女性/不养宠/要求年付)
  - **联系记录**: 回访结果模板, 电话接通/拒接/关机 (follow_type=录音跟进)
  - each record also has `follow_type`, `creator_name`, `role`, `created_at`, `labels` (真实在租… — 在租真实性信号), `remarks`, `on_top` (置顶的重点跟进)
- `hqi` — HQI 评分: `total_score`, `level`, `rank_text` 商圈排名, `heat_items`, `suggestions` AI 优化建议

**These are valid answers, not errors** (verified live 2026-08-09):

- `hqi: null` — house has no HQI score record yet.
- `follows: []` — no follow-ups exist yet.
- `has_survey_photo: false` with empty `photos` — house not surveyed yet.
- `maintain.remark` may be `null`; a field with `complete: false` shows `--` (not filled in).
- 托管 (trusteeship) listing ids fail the 普租 detail endpoints with an invalid-input error — do not retry them, and do not fall back to another listing.

Use the detail tools to settle questions the search catalog cannot answer: exact pet policy from `maintain` 养宠态度, key status from `get_detail.has_key`, survey state from `get_prospect`.

## Filters and budget

Call `rental_listing_filter_options` before using unfamiliar `condition_filters` keys or values. Use only the returned page-native keys and values.

- Convert an explicit room count to `condition_filters.bedroomAmount`.
- Convert rental mode to `condition_filters.rentType`: `001` for whole rent and `002` for shared rent, after confirming the current catalog values.
- For “N 元左右”, prefer `budget_yuan=N`: ordinary rent becomes `[N/2, N + clamp(25% of N, 200, 500)]`; shared rent has no lower bound by default.
- Preserve an explicitly stated price range instead of applying the budget heuristic.
- When combining radius + budget + pet: nearby first, then filter items by `price_text`/tags; if the circle comes up short, widen the radius before relaxing filters — and report what you relaxed and why.

For complex named-community queries, resolve the community through the map first, then use `rental_listing_search` so the full listing-filter catalog remains available.

## Reliability and privacy

Check connection status before a live query. If authentication is required, ask the user to scan using the connector's local login flow; never expose or persist credentials, QR contents, cookies, tokens, account IDs, or map keys.

Do not invent community IDs, filter keys, coordinates, or search results. Report ambiguity and empty results plainly, and offer the least-assumptive relaxation only after explaining it.
