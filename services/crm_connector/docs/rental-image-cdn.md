# 房源图片 CDN 访问规则（实测）

2026-08-10 通过 Playwright 注入 crm_connector 凭证，走真实列表页 → 房源详情页 →
列表/详情 API 全链路核实：**图片 URL 的获取需要凭证，图片本身的下载不需要凭证**。
原图 URL 按路径分桶保护——实勘/户型图桶对任何请求（含登录态）都是 403，封面桶公开；
公开下载的通用做法是拼接 CDN 尺寸后缀（任何桶的变体都公开）。

## 一、验证方法

1. `WindowsDpapiCredentialStore.load_active()` → `_decode_material()` 取 cookie，
   注入 Playwright context（`.link.lianjia.com` / `.ke.com` / `.lianjia.com`）。
2. 打开列表页 `/rent/house/list?isSaaS=false`，滚动触发懒加载，用 response 监听 +
   DOM `img.src` 收集图片 URL；再进入首个房源详情页重复采集。
3. 对收集到的每个 URL 做三种探测并对比：
   - A：完全裸请求（无 cookie、无 referer）
   - B：仅带 referer
   - C：带完整 cookie + referer
4. 另从列表 API（`/api/houseList/search/pc/list`）与详情 API（detailProspect）的
   JSON 响应中提取全部图片字段 URL，重复上述探测。

验证脚本：`run/verify_image_auth.py`（可重跑）；原始探测数据：
`run/image_auth_probe.json`。

## 二、图片 URL 的分布（哪些接口返回图片）

| 来源接口 | 字段 | 内容 |
|---|---|---|
| 列表 `houseList/search/pc/list` 行数据 | `titleImage` | 房源封面图 |
| 同上 | `floorPlanImage` | 户型图 |
| 详情 `detailProspect` | `houseProspectImageList[].prospectPicUrl` | 实勘照片 |
| 同上 | `huXingPicUrl` | 户型图（HDIC frame） |
| 同上 | `houseFrameImageResp.imageUrl` | 户型图（frame） |

图片全部托管在 `img.ljcdn.com`，按路径分为三类：
`/lease-image/house/`（封面）、`/110000-inspection/`（实勘，前缀为城市 code）、
`/hdic-frame/`、`/510100-frame/`（户型图）。

## 三、访问规则（核心发现）

### 1. 获取 URL 需要凭证

上述 API 均要求登录 cookie（现有 `authorized_fetch` 白名单链路已覆盖，无需改动）。

### 2. 原图 URL：按路径分桶保护（403 / 200）

`https://img.ljcdn.com//<path>/<file>.jpg`（API 原样返回）三种探测结果：

| 路径 | A 裸请求 | B 仅 referer | C 完整 cookie + referer |
|---|---|---|---|
| `110000-inspection/`（实勘） | 403（176B JSON） | 403 | **403** |
| `hdic-frame/`、`510100-frame/`（户型图） | 403（169B JSON） | 403 | **403** |
| `lease-image/house/`（对外展示封面） | **200 公开** | — | — |

带登录态的浏览器页面自身也无法加载受保护桶的原图——实测详情页对原图加载失败的房源
渲染 `noImage.014ee9f3.png` 占位图。**受保护桶的 403 是 CDN 层原图保护，不是业务鉴权，
凭证对此无效；`lease-image` 封面桶本身就是公开的**（营销展示图，也用于对外房源页）。
实勘照片（`110000-inspection/`）与户型图（`hdic-frame/`）是敏感桶，封面不敏感。
2026-08-10 复测 6 个 URL 确认分桶差异。

### 2.5 「展示设置」不是 200/403 的决定变量（用户假设验证，2026-08-10）

用户假设：基础信息「贝壳网/链家网 展示」决定封面原图是否公开，省心租（贝壳自营托管）
房源封面公开。实测 24 条房源（20 托管 + 4 普租，覆盖维护盘/角色房源两个范围）验证：

| 变量 | 实测 |
|---|---|
| 封面在 `lease-image` 桶（12 条，全为托管省心租） | 原图全部 200 |
| 封面在 `inspection` 桶（12 条，含 3 条 `extranetAppear=False` + 9 条 True） | 原图全部 403 |
| `extranetAppear=True`（外网展示）且封面在实勘桶 | **403**——反例：106128274229 双端展示（detailHead 有 `keUrl`+`lianJiaUrl`），封面原图仍 403 |
| 省心租·自营（tagList `贝壳省心租·自营`，24 条全部） | 12 条封面 200，12 条 403——取决于封面在哪个桶，与标签无关 |

结论：**决定性变量是桶，不是展示状态**。展示状态与桶强相关但非因果——展示中的房源
通常已上传对外营销封面（`lease-image` 桶，公开）；未上传对外封面图的房源，列表
`titleImage` 直接取实勘图（`inspection` 桶，受保护），即使它在贝壳/链家双端展示。
相关字段：列表行 `extranetAppear`（外网展示开关）、detailHead `keUrl`/`lianJiaUrl`
（外网链接，非空即双端展示）、`noShowKeReason`/`noShowLianJiaReason`（不展示原因，
实测恒为 null）。托管房源（delType=5）detailHead 空 data，展示字段拿不到，但列表行
`extranetAppear` 可作依据。验证脚本：`run/verify_display_image_policy.py`。

### 3. 尺寸变体 URL：完全公开

在原图 URL 后拼接尺寸后缀即可公开访问（无 cookie、无 referer），100% 复现：

| 后缀 | 结果 | 实测大小（750px 宽原图） |
|---|---|---|
| `（无）` | 403 | — |
| `.450x.jpg` | 200 | 31~99 KB（列表页缩略图即此形态） |
| `.750x.jpg` | 200 | 224 KB |
| `.800x.jpg` | 200 | 251 KB |
| `.1500x.jpg` | 200 | 774 KB |
| `.450x.png` | 200 | 166 KB（支持任意目标格式） |

页面实际请求的 `pc0_HUEypeSmt.jpg.450x.jpg` 正是"原图 URL + 后缀"的形态。
`/lease-image/`、`/110000-inspection/`、`/hdic-frame/`、`/510100-frame/` 全部适用
同一规则。10 个房源 × 户型图 + 90 个房源封面抽样验证无一例外。

## 四、水印情况

**已确认公开变体带水印**（用户于浏览器中目视确认，2026-08-10；AI 侧无法直接
目视渲染图片）。

推断依据（先于目视确认的推理链）：
1. 原图 403 + 变体公开 = 服务端转码管线（CDN 实时处理尺寸请求），水印几乎必然
   打在转码阶段——这也意味着水印不可能通过公开通道绕开。
2. 贝壳/链家对业务房源照片（实勘、封面）加水印是平台一贯做法。

统计验证的边界（诚实记录，不夸大）：
- 滑窗 NCC：两个不同房源封面在右下角存在 0.708 的强局部相关（两次独立实验重现
  0.55/0.47/0.70），与"共享角标"一致，但第三个样本不相关（0.25），不普适。
- 边缘密度：角落 < 中心（比值 0.13~0.73），排除大号文字横幅水印，与"小而半透明
  低对比度角标"一致。
- 结论：统计手段不能独立证明水印存在，以目视确认为准。

## 五、对接入的约束与建议

1. **下载图片不需要凭证**：拿到 URL 拼 `.750x.jpg` / `.1500x.jpg` 即可公开下载，
   无需代理 cookie、无需额外头；`lease-image` 封面桶连后缀都不用加，原图直连公开。
2. **无水印原图按桶而异**：`lease-image/house/` 封面原图公开可得（可能无水印，
   待目视确认）；实勘/户型图原图无公开通道（403 连登录态浏览器都过不去），产品如
   要求这些桶的无水印大图需另行评估（不承诺）。
3. 若连接器未来提供图片字段，建议**保留原始 URL**，由调用方自行拼后缀（原始 URL
   对任何人 403，返回时无需担心泄露受限内容）；或按用途直接透传页面同款
   `.450x.jpg`（缩略图）/ `.1500x.jpg`（高清图）形态。
4. 探测时注意 `img.ljcdn.com` 偶发 ConnectTimeout（机房链路抖动），需重试
   （本次 200/403 结论均在重试后稳定复现）。

## 六、验证脚本清单

| 脚本 | 用途 |
|---|---|
| `run/verify_image_auth.py` | 全链路验证：凭证注入 → 收集图片 URL → A/B/C 三探 → 结论输出 |
| `run/make_gallery.py` | 生成 `run/gallery.html`（8 张 1500x 样例内嵌展示页） |
| `run/images/` | 8 张已下载样例（4 套房源 × 封面/户型图，1500x） |
