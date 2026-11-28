# Moma / Falcon 平台待核实安全项清单（6 项）三期核实报告

> **日期**：2026-11-24　**视点**：办公网工作站匿名面（viewpoint B），全程无个人 Bearer/Cookie
> **范围**：`待核实安全项清单.md` 6 项（P0×3、P1×2、P2×1），仅使用自有测试应用 A（`60758c5d…`，文案生成器）与应用 B（`5610092c…`，房源视频生成器，外链 `/shipinshengcheng`）的公开发布材料
> **红线**：只读查询 + 合成输入 + 随机密文 + 零匹配参数；证据仅含状态码、业务码、字段名、长度、SHA-256、响应头属性；令牌/Cookie/apikey/签名/隐私原值一律不落盘（已通过统一脱敏门禁 `scripts/security/redaction_gate.py`；2026-11-24 起合并原 `redaction_selfcheck_r3.py`，当轮历史记录留存于 `evidence/round3/round3-redaction-selfcheck.json`）
> **新增请求量**：70 条（surface 10 + runtime 39 + decrypt/RLS 13 + expert 4 + cookie 4），单次无重试；静态核查 0 请求

## 0. 判定口径

沿用主报告三档：**风险成立** / **防线有效** / **阻塞**；部分维度成立记「部分成立」并拆开列证。

## 1. 结论总览

| 编号 | 待核实项 | 本期判定 | 一句话结论 |
| --- | --- | --- | --- |
| P0-1 | `/falcon/aroute/*` 5 接口匿名鉴权门 | **防线有效（匿名面）** | 两应用各 6 探针（含无签名负对照）全部 401，应用签名不能替代个人 Bearer；KE-01 不升级为匿名暴露 |
| P0-2 | 跨 `chat_id` 隔离 | **部分成立** | PostgREST 池 anon key 双向隔离有效；解密代理无个人鉴权、不校验表名，信封密钥可由公开 App ID 派生且缺失时仍放行 |
| P0-3 | 脚手架模板共性 | **风险成立（系统性）** | 两个独立应用同构复现公开 HMAC、宽 RLS、`ke_sess_bak`、解密代理与无 CSP；仅调试/管理面路由为应用 A 个案 |
| P1-4 | 同源多应用与 `.ke.com` Cookie 边界 | **部分成立** | 匿名面仅证「无个人 Bearer 一律 401」（匿名门有效）；网关是否误接受 `.ke.com` 主站 Cookie-only 身份**阻塞**——本轮视点 B 全程未携带真实主站会话 Cookie，无正向证据；另证匿名响应下发无 Secure/HttpOnly/SameSite 的 `.ke.com` 跟踪 Cookie，全部应用外壳无 CSP、bundle 无 DOMPurify |
| P1-5 | 预览路由与 Expert 授权边界 | **匿名面防线有效；发布态/创建者校验阻塞** | friday 预览生产宿主 302→`deploy_404`/404；4 个 Expert 管理 API 匿名 `errno=401`；未发布 Expert 的 UUID 级归属校验需第二 UUID/创作账号 |
| P2-6 | Falcon 全路径 CORS、缓存与配额归属 | **部分成立** | 4 条 CORS 探针**全部命中错误分支**（401、400、404、HTTP 200 但业务 `status=10001` 参数校验失败，无一条进入成功业务逻辑），但错误响应均无条件反射任意 Origin + `ACAC:true`，KE-18 治理面成立；带凭证跨域读**未获正向复现**（后续 6 条 `code=0` 解密请求均未携带 Origin 头）；`/falcon/city/list` 不校验 `X-App-Id` 合法性已证，计费类端点的配额归属阻塞 |

---

## 2. 逐项核实详情

### P0-1　`/falcon/aroute/*` 匿名鉴权门 —— 防线有效

**方法**：对应用 A、B 各自的 5 条接口（`users/page`、`users/detail`、`users/batch`、`ehr/user/superior`、`orgs/batch`）仅携带发布 bundle 的公开 HMAC 应用签名（`X-Signature/X-Timestamp/X-Nonce/X-App-Id`，无任何个人令牌），并对 `users/page` 补无签名/无 App-Id 负对照。参数全部合成或零 UUID。

**结果**（证据 `r3-runtime-probe-2026-11-24.json`，12 条探针）：

- 12/12 全部 HTTP 401，统一信封 `{"error":"缺少或无效的Authorization header"}`，两应用响应体 SHA-256 完全一致；
- 前端代码侧印证：`_t(appId)`/`ze(appId)` 从 `<appId>.supabase.auth.token` 取个人 Supabase JWT，取不到时 `Authorization/apikey` 头直接缺省——服务端在该分支拒绝，而非降级放行。

**结论**：Falcon 侧 aroute 个人事接口存在独立于应用签名的个人 Bearer 门，清单假设的「匿名人事目录暴露」不成立，**KE-01 维持「登录员工越权」原评级，不升级**。员工登录态后的字段/组织范围越权仍按主报告阻塞项（需第二授权账号）跟进。

### P0-2　多租户跨 `chat_id` 隔离 —— 部分成立

#### 2.1 PostgREST 池 anon JWT 隔离：防线有效

- 两个 bundle 各仅含 1 个 anon JWT，值不同（签名不同、`exp` 相差 34 天，仅记录 claim 形状与 SHA16）；
- 交叉探测：A 的 anon key 打 B 池、B 的 anon key 打 A 池（含根 OpenAPI）均 **401 `Unauthorized`（`WWW-Authenticate: Key`）**；无 key 为 401 `No API key found in request`；
- 两池 schema 独立：B 池根 OpenAPI 为 14 表 8 RPC（视频生成域，含 `smart_clip_jobs/properties/generated_videos/user_cities` 等），与 A 池（9 表 14 RPC，含 `admins/prompts/usage_logs`）无交集，B 池查 A 的 `prompts/admins/usage_logs` 返回 `PGRST205` 表不存在。
  - **台账截断说明（不受影响项）**：台账中的 `P03B-openapi-metadata` 经 `ProbeClient` 落盘时受 64KB 读取上限截断（响应头 `content-length: 72,412` 字节，台账 `body_len=65,536`、`json=false`），该截断副本**不用于**表/RPC 计数；脚本 `r3_decrypt_crosschat.py` 另起一次无截断的 `urllib` 拉取在内存中解析完整 OpenAPI，14 表 / 8 RPC 的数字来自这条完整响应（见台账 `app_b_schema_metadata.table_count=14` 与 8 条 `rpc_names`），结论不受 64KB 截断影响；但截断副本本身不保证可解析出全量定义，后续引用该路径元数据须以内存解析字段为准。

#### 2.2 应用 B 宽 RLS：风险成立（KE-21/27/28 跨应用复现）

anon key 只读 `limit=1` 探测 B 池真实表：`smart_clip_jobs`（987B）、`properties`（661B）、`smart_clip_music`（378B）均返回非空业务行；三张 `_app_role_*` 权限表返回空数组（2B）。应用 B 的匿名角色同样持有业务表读权，宽 RLS 不是应用 A 个案。

#### 2.3 解密代理归属校验：风险成立（KE-25/29 扩面）

修正 `rows` 为 round2 已验证可达处理逻辑的 `list[dict]` 契约后（证据 `r3-decrypt-crosschat-2026-11-24.json`，6 条合成密文探针）：

| 用例 | 结果 | 含义 |
| --- | --- | --- |
| B 的 chat_id + B 的信封密钥 | 200 `code=0`（`envelope` 信封） | 合成密文被正常受理 |
| B 的 chat_id + A 的密钥 / A 的 chat_id + B 的密钥 / 零 UUID+admins+A 密钥 | 400 `code=1`「X-Envelope-Key 与 chat_id 不匹配」（三者体 SHA 相同） | **带密钥时** chat_id↔密钥绑定被校验 |
| B 的 chat_id + 不带信封密钥 | 200 `code=0`，回显入参结构 | 密钥头非必填，绑定校验可直接绕过 |
| 不存在表名 + 正确密钥 | 200 `code=0` | 不校验表名白名单/归属 |

**结论**：跨租户的唯一屏障是 `X-Envelope-Key` 与 `chat_id` 的匹配，而该密钥就是 `btoa(utf8(app_id))`（前端公开函数，App ID 在所有发布物中明文可见），且不带密钥仍返回 `code=0`。解密代理无个人鉴权、无表/行归属校验，**KE-25/29 在第二个应用上原样复现，风险成立**；真实明文解出仍需业务方提供可销毁密文 fixture（沿用主报告阻塞口径）。

### P0-3　平台脚手架模板共性 —— 风险成立（系统性）

对两个独立构建、独立租户的 Lite App 做静态构建抽查（证据 `r3-surface-fetch-2026-11-24.json`、`r3-cookie-static-2026-11-24.json`，0 请求）：

| 脚手架要素 | 应用 A（文案） | 应用 B（视频） | 判定 |
| --- | --- | --- | --- |
| 公开 HMAC 密钥常量（JWT 头 `eyJhbGci…J9` 作 HMAC secret 的独立常量定义） | **6 处**（原始字面量命中 7 次，见下注） | 4 处（原始命中 5 次） | 系统性 |
| `MOMA-NO-SIGN-WARNING` 降级标记 / 仅 pathname 签名 | 有 | 有（同构 `Se/Be/Ve`） | 系统性 |
| `ke_sess_bak` 7 天 Cookie（`168*3600`，`path=/`，`SameSite=Lax`，无 Secure/HttpOnly） | 有 | 有（逐字同构，仅压缩变量名不同） | 系统性 |
| `supabase-volc` 代理 + `supabase-crypto/decrypt` | 有 | 有 | 系统性 |
| aroute 五接口类（`Li`/`Bo`，方法与错误文案逐字相同） | 有 | 有 | 系统性 |
| 宽 RLS（anon 可读业务表） | 运行时已证（round2） | 运行时已证（本期 2.2） | 系统性 |
| CSP / DOMPurify | 无 / 无 | 无 / 无 | 系统性缺失 |
| `admin_session` / `password-debug` 调试管理路由 | 有 | **无** | 应用 A 个案（与该应用内置管理台功能相关） |

> **HMAC 计数口径统一（与主报告 r7 第 3.2 节「6 处字面量」对齐）**：对本地 bundle 全文匹配 JWT 头部常量 `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9` 的原始命中次数为应用 A 7 次、应用 B 5 次；但两应用各有 **1 次命中是 Supabase anon JWT 的头部前缀**（形如 `An="eyJ…J9.eyJpc3MiOiJzdXBhYmFzZS1wbGF0…"`，该字符串是完整 anon JWT 的第一段，并非 HMAC 密钥定义）。剔除 anon JWT 前缀后，**作为 HMAC secret 的独立常量定义为应用 A 6 处、应用 B 4 处**，与主报告 r7「公开前端 6 处字面量使用同一 JWT 头部常量作为 HMAC 密钥」一致；应用 B 的 4 处为同构模板复现。此前 P0-3 表所写「7 处/5 处」是未区分两类用途的原始字面量计数，本期起统一按「HMAC 密钥常量」口径列报，原始命中数保留在括号内备查。

**结论**：清单关注的四个默认产物中，公开 HMAC、宽 RLS、`ke_sess_bak` 三项为平台模板系统性生成；调试路由仅出现在带管理台的应用 A，非全量默认。新建空白 Lite App 的对照需平台创作账号，记阻塞；双应用同构已足以支撑系统性结论。

### P1-4　同源多应用隔离与 `.ke.com` Cookie 边界 —— 部分成立

**运行时**（证据 `r3-cookie-runtime-2026-11-24.json`，4 条匿名探针）：

- 匿名访问 app-moma 根、`/falcon/city/list`、`/shipinshengcheng` 均下发 `lianjia_ssid` 与 `lianjia_uuid`，作用域 `domain=.ke.com; path=/`，属性仅 `expires/Max-Age/domain/path`——**无 Secure、无 HttpOnly、无 SameSite**；PostgREST 401 响应不下发 Cookie；
- 匿名响应不下发任何认证类 Cookie；`prd-assistant-token-prod` 在两个应用 bundle 中 0 引用（HttpOnly，UGC 前端 JS 不可读，与主报告属性表一致）。

**网关是否误接受主站 Cookie（阻塞，未做正向验证）**：浏览器确会把 `.ke.com` 父域 Cookie 自动带往 `app-moma.ke.com`，但本轮全部运行时证据取自视点 B 匿名面（两份台账 `meta.identity_plane` 均为 `anonymous; no personal Bearer/Cookie attached`、`no personal Cookie sent`），**从未携带真实的 `.ke.com` 会话 Cookie（如 `prd-assistant-token-prod`）访问过 Falcon 网关**。P0-1 的 aroute 401 只能证明「无个人 Bearer 的匿名请求被拒」即匿名门有效，**不能外推为「网关不接受 Cookie-only 身份、攻击链关闭」**——不带 Cookie 返回 401 与带主站 Cookie 时是否被误认身份是两个命题。该问题改标**阻塞**，解锁路径：恢复凭证后用浏览器 harness 携带真实主站 Cookie 直连 Falcon 路由做正向验证，或由平台研发白盒确认网关鉴权中间件是否解析 Cookie 头。

**UGC 前端隔离**：app-moma 外壳、应用 B 外壳、moma Expert 外壳响应均无 CSP/XFO/nosniff（运行时响应头 + 两个 bundle 静态 0 引用），应用 bundle 无 DOMPurify。平台对用户生成应用前端**没有 CSP 隔离或净化基线**，与 KE-20/KE-31 同根，风险成立。

**阻塞**：登录态下浏览器对预览路由的实际 Cookie 携带截图级证据，需重新扫码恢复凭证后用浏览器 harness 补测（主报告阶段 4 已登出吊销）。

### P1-5　预览路由与 Expert 入口授权边界 —— 匿名面防线有效，深层阻塞

- `app-moma.ke.com/api/gateway/friday/v1/product/preview`：GET/POST 匿名均 **302 → `moma.ke.com/deploy_404`**（平台统一未部署占位，带 `no-store`）；`prd-assistant-backend` 同路径 404。匿名无法触达预览业务逻辑；
- Expert 外壳：有效 UUID 与零 UUID 返回**逐字节相同的 19588B SPA 外壳**（SHA-256 一致），外壳仅含 `__INITIAL_THEME__`，无内联业务数据，数据全部靠登录后 XHR 加载——外壳 200 不构成数据越权；
- Expert 管理类 API（`/api/experts/{id}/api-services`、`.../api-service-candidates`、`/api/user/api-services/{id}/experts`、`/api/experts`）匿名 4/4 返回 HTTP 200 信封但 **`errno=401`**（与 KE-12 同口径的业务级匿名门）；
- 前端管理 SPA 存在 `expert_access.read/write`、`expert_create/edit` 权限标记（仅 UI 侧，服务端门已在上一条观测）。

**阻塞**：未发布 Expert 是否仅凭 UUID 可读、创建者/组织级校验是否生效，需要第二个（自有未发布）Expert UUID 或平台创作权限；本期不做「不存在绕过」结论。

### P2-6　Falcon 全路径 CORS、缓存与配额归属 —— 部分成立（CORS 反射成立；带凭证跨域读与计费配额归属阻塞）

**CORS 扩面**（证据 `r3-runtime-probe-2026-11-24.json`，4 路径 + 1 预检）：

| 路径（方法/HTTP 状态） | 实际命中分支 | ACAO 反射 `evil.example` | ACAC | Vary | Cache-Control |
| --- | --- | --- | --- | --- | --- |
| `/falcon/aroute/users/page`（GET/401） | 匿名鉴权拒绝（`缺少或无效的Authorization header`） | 是 | true | `Origin` | `no-store` |
| `/falcon/house/zu/house_info`（GET/400） | 业务错误，回显 Python 内部异常原文 `'NoneType' object has no attribute 'get'`（归入 KE-15 错误信息内部化） | 是 | true | `Origin` | `no-store` |
| `/falcon/supabase-volc/<B>/rest/v1/prompts`（GET/404） | PostgREST `PGRST205` 表不存在 | 是 | true | `Origin` | 未声明 |
| `/falcon/supabase-crypto/decrypt`（POST/**HTTP 200**） | **业务失败**：信封 `status=10001`、`msg="Field=>rows RequestValidationError: Input should be a valid dictionary"`，`data_shape=NoneType`，请求未进入解密逻辑（与本文件前半段 4 条 P02-decrypt-* 同属 `rows` 结构发错） | 是 | true | `Origin` | 未声明 |

**口径修正（证据边界）**：

- 4 条 CORS 探针**没有一条进入成功业务分支**，状态分别为 401、400、404 以及「HTTP 200 外壳 + 业务 10001 参数校验失败」。因此**不得**表述为「含成功 200 的 decrypt」或「4/4 复现带凭证跨域读」。
- 后续 `r3-decrypt-crosschat-2026-11-24.json` 中修正 `rows` 契约后拿到 `biz_code=0` 的 6 条解密请求（D1–D6），其 `request_header_names` 仅含 `Accept/Content-Type/User-Agent[/X-Envelope-Key]`，**均未携带 `Origin` 头**，同样不能作为跨域读取的正向证据。
- **仍然成立的部分**：网关在**错误响应上无条件反射任意 Origin 并回 `ACAC:true`**（4/4，含预检 OPTIONS 对 `evil.example` 反射并允许全部方法），说明 CORS 反射逻辑不区分成功/失败分支，归入 **KE-18 治理范围**；但「攻击者页面能否带凭证读到成功业务响应体」仍是**阻塞项**，需在授权窗口用受控跨域页面或带 Origin 的成功探针补证。
- 本批路径均带 `Vary: Origin`，KE-32 型「缺 Vary 缓存投毒」未在新路径复现（`city/list` 的仅 `Accept-Encoding` 问题以 round2 台账为准）；decrypt 与 PostgREST 错误响应缺 `Cache-Control`。
- 补充低危细节（KE-18/KE-15 旁证）：应用 B 池 `smart_clip_jobs`（987B）与 `properties`（661B）的**非空业务行**响应头为 `Vary: Accept-Encoding` + `ACAO: *`（无 `Origin`），而同批空表响应（2B，`_app_role_*` 三表）为 `Vary: Origin` + `ACAO: *`——同一池的 CORS/缓存响应头随响应内容不一致，且非空表缺少 `Vary: Origin`，建议纳入 KE-18/KE-32 统一整改。

**`X-App-Id` 配额绑定（结论收敛）**：对**静态公开城市字典端点** `/falcon/city/list` 分别发送正确 App A ID、应用 B ID、零 UUID、缺省四种 `X-App-Id`（签名其余部分不变），4 次响应均 200 且**响应体 SHA-256 与长度逐字节一致**（29458B，哈希 `c9fcfc34…6c885`）。该结果**仅能证明**：Falcon 网关在 `/falcon/city/list` 这一公开字典路由上不校验 `X-App-Id` 的合法性/存在性。

- **不能据此推出**计费类 AI 端点（`/falcon/ai_endpoints/v1/*`）同样不把 `X-App-Id` 用于后端配额归属或账单审计——`city/list` 本身不涉及模型计费与配额扣减，两类路由的服务端处理链可能不同。
- 计费端点的 `X-App-Id` 配额归属改标**阻塞 / 待服务端配置核实**（需平台侧配额/账单配置白盒或授权窗口的计费归因实测）；round2 已证的 Nonce 不去重与 M4 并发熔断缺失仍是独立的限流风险证据，不依赖本项推导。

---

## 3. 阻塞项与复测条件（不并入通过）

| 阻塞项 | 解锁条件 |
| --- | --- |
| 未发布 Expert / 预览态的创建者与组织级权限 | 自有第二 Expert UUID（含未发布态）或平台创作账号 |
| 登录态 Cookie 携带的浏览器级证据、KE-01 员工横向 BOLA | 人工重新扫码恢复凭证库，跑修复后的 `ke_round2_browser_harness.py` |
| **Falcon 网关是否误接受 `.ke.com` 主站 Cookie-only 身份（P1-4 改标）** | 恢复凭证后用浏览器 harness 携带真实主站会话 Cookie（如 `prd-assistant-token-prod`）直连 Falcon 路由做正向验证；或平台研发白盒确认网关鉴权中间件是否解析 Cookie 头。本轮匿名面 401 仅证匿名门有效，不构成「Cookie-only 被拒」的正向证据 |
| **计费类 AI 端点（`/falcon/ai_endpoints/v1/*`）的 `X-App-Id` 配额/账单归属（P2-6 改标）** | 平台侧配额/账单配置白盒，或授权窗口对计费端点做不同 `X-App-Id` 的归因实测；`city/list` 同体响应仅证公开字典路由不校验 App ID，不能外推计费链路 |
| **CORS 带凭证跨域读成功业务响应（P2-6 改标）** | 授权窗口用受控跨域页面或携带 `Origin` 的成功探针补证；本轮 4 条 CORS 探针全部命中错误分支、6 条 `code=0` 解密请求均无 Origin 头 |
| 解密真实明文归属判定 | 业务方提供可销毁密文 fixture |
| 空白 Lite App 模板对照 | 平台创作账号新建空白应用 |
| 双账号跨 `chat_id` 登录态隔离 | 第二授权员工账号 |

## 4. 证据与脚本索引（round3）

| 文件 | 内容 | 请求数 |
| --- | --- | --- |
| `evidence/round3/r3-surface-fetch-2026-11-24.json` | 应用 B 外壳/bundle/Expert 外壳抓取与 JWT 形状、安全头 | 10 |
| `evidence/round3/r3-runtime-probe-2026-11-24.json` | P0-1/P0-2 池交叉/P1-5/P2-6 运行时主台账 | 39 |
| `evidence/round3/r3-decrypt-crosschat-2026-11-24.json` | 解密跨 chat_id 六用例 + B 池 schema 元数据 + B 池 anon RLS | 13 |
| `evidence/round3/r3-expert-gate-2026-11-24.json` | Expert 管理 API 匿名门 | 4 |
| `evidence/round3/r3-cookie-runtime-2026-11-24.json` | 匿名 Set-Cookie 属性形状 | 4 |
| `evidence/round3/r3-cookie-static-2026-11-24.json` | 双应用 `ke_sess_bak`/CSP/DOMPurify 静态比对 | 0 |
| `evidence/round3/round3-redaction-selfcheck.json` | 脱敏自检结果 | — |
| `scripts/security/round3/` | `ke_round3_lib.py` 及 5 个探针脚本 + 自检脚本 | — |
| `outputs/moma-r3-surface-2026-11-24/` | 应用 B 与 Expert 发布物本地副本（公开前端工件） | — |

## 5. 修复建议增量（在主报告第 6 节路线图上追加）

1. **解密代理**：`X-Envelope-Key` 设为必填并与个人会话联合鉴权；密钥服务端化，禁止 `btoa(app_id)` 派生；增加表名白名单与行级归属校验，无密钥/错密钥统一 401 而非 `code=0`。
2. **anon RLS 模板**：新建 Lite App 默认拒绝 anon 业务表读权，模板级 RLS 基线纳入发布门禁（B 应用已复现 `smart_clip_jobs/properties` 匿名可读）。
3. **CORS 基线**：Falcon 全路径改为 Origin 显式白名单、禁反射与 `null`，错误响应同样适用；decrypt/PostgREST 响应保持 `Vary: Origin` 并统一补 `Cache-Control: no-store`；应用 B 池 `smart_clip_jobs/properties` 等非空业务表响应补齐 `Vary: Origin`，消除与同池空表响应的头策略差异。
4. **配额归属（加固建议，计费链路证据阻塞）**：`X-App-Id` 与服务端租户/配额强校验，非法/缺省 ID 拒绝计费类请求；配合 Nonce 去重与并发熔断。注意：本轮仅实证公开字典路由 `city/list` 不校验 App ID，计费端点是否以 App ID 做配额归属仍待服务端配置核实（见 §3 阻塞表），本建议按纵深防御提出，不宣称计费绕过已证。
5. **Cookie/CSP 模板**：停止在 `.ke.com` 下发无安全属性的 `lianjia_ssid/uuid`；Lite App 外壳默认注入 CSP、前端模板内置净化库；`ke_sess_bak` 淘汰或加 Secure/HttpOnly 等价存储。
6. **Expert/预览**：服务端对未发布 Expert 强制创建者/组织校验并在 UUID 枚举时返回统一 404；预览路由在生产宿主保持关闭的同时补显式鉴权门。
