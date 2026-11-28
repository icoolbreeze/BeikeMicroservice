# 豌豆 PC：CRM 令牌复用及只读业务接口实测

验证日期：2026-11-20  
站点：`https://wandou.ke.com/chat`  
性质：当前版本、单个已授权员工账号的小样本验证，不是厂商正式 OpenAPI。  
阅读入口：[凭证复用总览](../../../docs/ke-app-credential-reuse.md)。

## 结论与实际身份

CRM 的当前 `puzu_lease_token` 被豌豆服务端接受，能够读取当前账号真实客源与在售房源数据。

验证不是只在前端放置一个 token：`GET /api/v1/users/me` 返回 200、`success=true`、`code="0"`，其 `data.ucid` 与 CRM 凭证主体一致；当前账号城市为成都。相同业务接口不带令牌为 401/`U001`，带令牌后返回非空业务数据。

页面验收：`/chat` 保持可访问，手机号验证码表单消失，出现对话输入框；刷新后仍正常，观察到的相关接口无 401/403。没有发送短信、伪造 JWT 或修改角色。

## 认证材料和正常请求

前端原生读取 `localStorage.token`，由请求拦截器附加 Bearer 与客户端标识。只在豌豆 origin 初始化真实的当前令牌：

```text
origin: https://wandou.ke.com
localStorage key: token
value source: 现有 CredentialStore 动态提供的 puzu_lease_token
```

接口头模板：

```http
Authorization: Bearer <当前有效的 puzu_lease_token>
X-Wandou-Client-Id: wandou-pc
Content-Type: application/json
```

浏览器初始化可使用隔离上下文，在页面加载前针对精确 origin 写入该存储键。API 使用只读请求即可，不需要先填手机号或验证码。用户资料由真实 `/api/v1/users/me` 返回，不需要自己拼造 `userInfo`。

| 尝试 | 结果 |
| --- | --- |
| 只保留 TGC 或共享已登录 Moma 的 Cookie | 未自动建立豌豆会话，停留在 `/login?redirect=...` |
| `security_ticket` 作为 Bearer | 401、`U001` |
| `saas_token` 作为 Bearer | 401、`U001` |
| `puzu_lease_token` 作为 Bearer | 身份、配置及下述业务接口成功 |

这是当前后台兼容性的实测结果。没有得到厂商“永久支持所有 CRM 令牌”的承诺；没有测试真实 token TTL、长期续期或并发。失效后应先通过既有 CRM 流程刷新，取得新令牌再做一次业务复测。

## 实测业务端点

所有路径均相对于 `https://wandou.ke.com`。下述 POST 是前端实际使用的查询接口；未调用创建、关联、删除、跟进写入或导出分享接口。

| 方法 | 路径 | 参数/请求体 | 结果 |
| --- | --- | --- | --- |
| GET | `/api/v1/users/me` | 无 | 200；当前用户主体与 CRM 一致 |
| POST | `/api/v2/notes/customers/search` | `{"pageNo":1,"pageSize":1}` | 不带 token 为 401；带 token 为 200、业务码 0；总数 2，返回 1 条 |
| GET | `/api/v2/house/resource/default/search` | `query=华润&page=1&size=1` | 不带 token 为 401；带 token 返回 1 套，总数 4477、`hasMore=true` |
| GET | 同上 | `query=华润&page=2&size=1` | 200；返回第 2 页，编号不同于第 1 页，总数仍为 4477 |
| POST | `/api/v2/house/resource/batch` | 见下方回查模板 | 200；返回 1 套，编号与列表结果一致 |
| GET | `/api/v2/house/resource/negotiation/page` | `managerUcId=<当前账号 UCID>` | 两次均为 HTTP 502、`text/html`；未通过 |

不要对未来的复测固定断言“2”或“4477”；这两个值是当日、当前账号和该查询条件下的快照。

### 身份响应

实测包络包含：

```json
{
  "success": true,
  "code": "0",
  "message": "成功",
  "data": {
    "ucid": "<当前账号>",
    "userCode": "<当前账号编码>",
    "name": "<当前账号姓名>",
    "cityCode": "<当前城市编码>",
    "cityName": "<当前城市>",
    "positionCode": "<岗位编码>",
    "positionName": "<岗位名称>",
    "positionType": "<岗位类型>"
  }
}
```

以上只是字段结构模板，所有个人字段均为占位符。必须将主体与当前 CRM 凭证在内存中对照，不能把本地用户姓名、历史会话或页面标题当作身份依据。

### 客源列表

请求：

```http
POST /api/v2/notes/customers/search
```

```json
{"pageNo": 1, "pageSize": 1}
```

响应业务结构：

```json
{
  "success": true,
  "code": "0",
  "data": {
    "totalCount": "<动态数量>",
    "customers": [
      {
        "customerId": "<客源编号>",
        "name": "<客源姓名>",
        "avatar": "<头像>"
      }
    ]
  }
}
```

本次第一条记录只有 `customerId/name/avatar` 三个字段。没有验证手机号、需求预算、跟进状态、跟进详情或其读取权限；不能把前端类型定义中的可选字段当成实际已返回数据。仓库中不保留真实客源编号、姓名、头像地址或原始响应。

### 在售房源搜索与分页

```http
GET /api/v2/house/resource/default/search?query=<关键词>&page=1&size=1
```

`data` 包含 `list/total/page/size/hasMore`。先检查业务成功字段，再读取分页。实测第 1、2 页各返回一条，编号不同；没有遍历全部结果。

本次结果分类：

```json
{
  "businessType": "SALE",
  "businessResourceType": 0,
  "housedelType": 1
}
```

列表中观察到的字段：

- `housedelCode`、`housedelType`、`businessType`、`businessResourceType`；
- `resblockName`、`roomLayout`、`orientation`、`floor`、`buildArea`；
- `price`、`unitPrice`、`houseImage`、`imageUrl`。

字段大小写应保持原样，尤其是 `housedelCode` 和 `housedelType`。价格单位未在本次验证中单独核实，不要在 Adapter 中未经确认就当作租金或总价万元处理。

### 单条回查

使用刚刚由豌豆列表返回的编号：

```http
POST /api/v2/house/resource/batch
```

```json
{
  "housedelCodes": ["<豌豆列表刚返回的 housedelCode>"],
  "businessResourceType": 0
}
```

实测 `data` 为数组，长度 1，编号与搜索结果一致；返回字段为：

`housedelCode/housedelType/businessType/resblockName/roomLayout/orientation/floor/buildArea/price/unitPrice/houseImage`。

这里只证明结构化单条回查；不是完整详情、实勘、跟进或全字段归档验收。

### 本人维护房源：失败记录

```http
GET /api/v2/house/resource/negotiation/page?managerUcId=<当前账号 UCID>
```

两次返回 502，Content-Type 为 HTML，未取得业务 JSON。原因未查明；不能将其解释为“当前账号没有维护房源”，也不能将 HTML 502 误报为 token 失效。

目前保持该接口不可用的测试标记。后续先核查正常页面与上游状态，再决定是否复测；不要在没有新证据时反复重新登录。

## 只从前端发现、尚未实测的读取能力

| 方法 | 路径 | 前端调用形式 | 验证状态 |
| --- | --- | --- | --- |
| GET | `/api/v2/house/resource/search_for_type1` | 查询参数 `query` | 未实测，不推断对应业务类型及权限 |
| POST | `/api/v2/house/resource/batch_for_type2` | `delCodeList`、`delStatusList:[1]` | 未实测，不作为租赁已打通的依据 |
| POST | `/api/v2/notes/houses/history` | `noteId` | 未实测，不读取无关笔记或据此扩大范围 |

发现路径不等于接口可用；不要将这些条目加入“已验证生产能力”。

## 认证之外，首页已验证的读取

下列接口也在真实前端加载中返回 200：

- `GET /api/v2/notes/business-config`；
- `GET /api/v3/aschat/config`；
- 当前用户主会话的 records 查询；
- `GET /api/v3/aschat/inspirations`。

会话记录属于当前用户数据，不在本文件保存会话 ID、内容、请求 ID 或原始响应。本轮业务验证直接调用上述结构化业务接口，没有依赖聊天模型的回答，也没有发送测试业务指令来修改数据。

## 复测最小步骤

1. 通过项目既有认证边界取得当前有效的 `puzu_lease_token`；值只留在内存，限定发送到豌豆域。
2. 调用 `/api/v1/users/me`，确认 HTTP/业务状态、UCID 一致性。
3. 不带令牌和带令牌分别调用客源或房源查询，确认负对照 401 与正对照非空业务数据。
4. 房源每页先取 1 条；比较第 1/2 页编号，再对第一条做 batch 回查。
5. 对个人字段只输出脱敏摘要、字段名和条数，不输出原始客源或完整响应。
6. 对 401/`U001` 与 502 分开记录，避免用重复登录掩盖业务上游错误。

后续若产品化，新增 Wandou Adapter 需要扩展现有 `SessionProvider` 的受控 origin/route 和输出契约；本记录没有改变这些生产限制，没有新增 HTTP/MCP 工具。

## 研究来源

- 豌豆当日公开前端构建 `index-DRg1AovW.js`：Bearer 注入、`X-Wandou-Client-Id`、只读查询的路径和参数。
- `auth-wiHuy4qG.js` 与登录页面模块：当前公开 PC 登录页为手机号验证码，成功后将返回的 token/accessToken 存入 `token`。
- 隔离 Edge 浏览器的首页、刷新、身份和真实业务查询，以及不带凭证的负对照。
- 经过用户授权使用已有 CRM 材料；没有在文档或仓库复制实际凭证、个人资料或业务原始响应。

这些构建名仅帮助定位当日证据，不应作为长期固定下载路径。
