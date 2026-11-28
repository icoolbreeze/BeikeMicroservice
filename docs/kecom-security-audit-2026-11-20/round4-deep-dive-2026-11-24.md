# ke.com 第四期深度核查报告：短信爆破 / 数字 ID 与分享码 / CAS 前置校验 / 限流产线生效性（2026-11-24）

> **定位**：本文是主报告（`kecom-security-audit-report-2026-11-20.md`，下称「主报告」，r7 已并入本文结论）的四期专题深挖，**不替代主报告编号**。2026-11-24 核查整改后，本文条目分三层列报：**R4-S01~S04 为本期新增离线发现**（静态/架构线索，前期报告无对应项）；**R4-B01~B07 为阻塞待证假设**，全部显式挂靠主报告既有编号或标注为新增假设，不另立风险条目；主报告已证项的复核只做工具映射，不重复编号。沿用主报告「风险成立 / 防线有效 / 阻塞」三档口径。
> **红线与脱敏**：本轮全部为离线静态分析、历史证据量化复核与 dry-run 探针计划，**实时请求数 = 0**；所有探针默认 dry-run，真实执行需显式环境变量解锁且受每端点 ≤12 次硬上限约束；证据只存状态码/业务码/字段名/长度/SHA-256/响应头属性，不含手机号、验证码、Token、Cookie、签名原值。证据目录与本报告均通过统一脱敏门禁 `scripts/security/redaction_gate.py` 自检（2026-11-24 起合并原 `r4_redaction_check.py`，产物 `redaction-gate-selfcheck.json`；当轮历史记录留存于 `evidence/round4/round4-redaction-selfcheck.json`）。

---

## 0. 本轮方法与产物索引

| 产物 | 路径 | 性质 |
| --- | --- | --- |
| 离线特征提取（628 切片，16 个 bundle/HTML，0 请求；**工作产物，非证据台账**，2026-11-24 核查后移出 evidence/，避免原始代码切片混入证据门禁） | `outputs/security-round4-workproduct/r4-offline-extract.{json,txt}` | 静态工作产物 |
| 历史台账量化统计（脚本可解析子集 **255 条**，非全证据库；口径见 §4.1） | `evidence/round4/r4-evidence-stats.json` | 静态复核 |
| 短信爆破面探针（dry-run 计划 12 项） | `evidence/round4/r4-sms-probe-dryrun.json` | 待授权执行 |
| 数字 ID / 分享码探针（dry-run 计划 39 项） | `evidence/round4/r4-idor-token-probe-dryrun.json` | 待授权执行 |
| CAS KE-11 探针（dry-run 计划 19 项） | `evidence/round4/r4-cas-probe-dryrun.json` | 待授权执行 |
| 限流覆盖矩阵（dry-run 计划 136 项 / 9 单元） | `evidence/round4/r4-ratelimit-coverage-dryrun.json` | 待授权执行 |
| 脱敏自检结果 | `evidence/round4/round4-redaction-selfcheck.json` | 门禁 |
| 探针源码 | `scripts/security/round4/r4_*.py` | 可复跑工具 |

---

## 1. 短信验证码在线爆破与频控绕过（send_code / verify_code）

### 1.1 已坐实的事实基线

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 两接口在 staging OpenAPI 均为 POST（各 1 个 endpoint），同目录存在 6 操作的管理面 `/api/admin/customer-sms-whitelist`（GET/POST/DELETE） | `staging-map.json` L1288-1294、L215 | 契约存在 |
| 生产匿名 `POST /api/customer_auth/send_code` 空体返回 **HTTP 422**（参数校验先于业务），Moma 后端与豌豆两个出口同形 | `sidechannel-anon-probe.json` L220-224、L271-275 | 防线有效（schema 层） |
| M5 短信频控测试因授权窗口未开 **0 请求跳过**，无任何发送/校验频控运行时数据 | `round2-session-2026-11-23-sms.json`（request_total=0） | **阻塞（主报告 M5 / `ROUND2_SMS`）** |
| round2 会话脚本已内置 XFF 对照骨架：同手机号连发 3 次，第 2/3 次带 `X-Forwarded-For: 198.51.100.10/11`（RFC 5737 文档示例地址，非真实 IP），但从未实跑 | `ke_round2_session.py` L458-470 | 工具就绪 |
| 管理后台前端存在独立 chunk `CustomerSmsWhitelistManagement-*.js`，路由 meta 描述为「访客短信登录手机号 Redis 白名单」，`requiresForceAuth` | `outputs/security-round4-workproduct/r4-offline-extract.json` sms 族（expert-index 切片） | 白名单是硬门禁线索 → **R4-S03** |
| 同 App 内 Supabase GoTrue 电话 OTP 通道客户端契约含 `gotrue_meta_security.captcha_token`，但 captchaToken 为可选字段 | ship-supabase / moma-supabase bundle 切片 | CAPTCHA 可选风险 → **R4-B02** |

### 1.2 爆破空间数学（核查要点：验证码长度）

| 长度 | 候选空间 | 熵 | 10 req/s 单线程 | 1000 req/s（100 并发） |
| --- | --- | --- | --- | --- |
| **4 位** | 10,000 | 13.3 bit | 16.7 分钟 | **10 秒** |
| 5 位 | 100,000 | 16.6 bit | 2.8 小时 | 1.7 分钟 |
| **6 位（当前行业下限）** | 1,000,000 | 19.9 bit | 27.8 小时 | 16.7 分钟 |
| 8 位 | 100,000,000 | 26.6 bit | 115 天 | 27.8 小时 |

结论：**4 位验证码在无错误尝试上限时属于可在线爆破区间**；即使 6 位，只要单码允许无限错试且无 IP/手机号维度频控，1000 req/s 下 17 分钟可穷尽。验证码长度本身不构成防线，**「单码错误尝试上限（3–5 次作废）+ 发送间隔 + 校验频控」三件套才是**。

### 1.3 本期判定（编号已按三层 taxonomy 重整）

- **R4-B01（阻塞待证假设，挂靠主报告阻塞表 M5 短信 / `ROUND2_SMS`，非 KE-26）短信爆破面未闭环**：schema 422 只证明空参被拒，不能证明任何频控/锁定/TTL 语义。五个关键未知全部阻塞：① 验证码长度；② 有效期；③ 单码最大错误次数；④ 单手机号/单 IP 发送与校验频率；⑤ 服务端是否信任客户端 XFF。解锁探针：`r4_sms_probe.py`（S1 schema / S2 状态机 oracle / S3 长度推断 / S4 双臂发送频控 / S5 错试上限），需 `R4_RUN=1 R4_SMS_PHONE=<授权号> R4_SMS_LIVE_SEND=1`。
- **R4-B02（阻塞假设，M5 附属）CAPTCHA 字段可选化**：GoTrue 电话通道把 `captcha_token` 设计为可选入参；若后端同样不强制，则短信登录分支无机器人校验。需在 S4/S5 证据的 `text_*` 字段中核对是否出现 captcha/滑块/风控标记（探针已内置 `RISK_MARKERS` 观察清单）。
- **R4-S03（新增架构线索·中）白名单暗示默认拒绝、灰度放行**：见 §5 新增发现表。风险在于白名单窗口一旦对生产放开，而频控规则未同步从灰度切全量，即形成完整爆破面。**开关存在 ≠ 强制启用，需在授权窗口验证默认（非白名单）手机号的拒绝语义与拒绝前是否消耗短信配额**。

### 1.4 XFF 信任面专项

- 静态面：全部 16 个前端 bundle 中 **0 处业务代码读取 `X-Forwarded-For/X-Real-IP/X-Client-IP`**（xff_ip 族仅 4 命中，全部是腾讯云 COS SDK 内部的 `clientIP` 签名参数，与限流无关）。说明伪造 XFF 不会被前端拦截，但**服务端是否采信只能由 S4 双臂实验判定**：直连臂被限流而 XFF 旋转臂持续 200 ⇒ 服务端以客户端头做限流键（风险成立）；两臂同形态 ⇒ 限流键取真实连接 IP（防线有效）。
- 修复基线：限流键必须取最外层可信代理写入的连接 IP（网关侧 `remote_addr`/`X-Forwarded-For` 最后一跳并配可信代理白名单），禁止直接透传客户端自报头。

---

## 2. 整数型业务 ID、分享码与邀请码可猜测性

### 2.1 数字型业务 ID（豌豆 noteId/fileId/sessionId/userId、房源 house_id/pid）

| 事实 | 证据 | 判定 |
| --- | --- | --- |
| 豌豆会话列表 `sessionId` 为 int、客源 `customerId` 为 int；笔记/文件链路 `noteId`/`fileId`/`fileApiId` 全为数值透传 | `idor-probe.json` C1/C2（sessionId:int，实测 6 条会话）；`wandou-param-shapes.txt`；离线 numeric_id 族 191 切片（工作产物） | 短整数 ID 面成立 |
| 豌豆单账号 BOLA 门：合成 streamId 返回 `400 INVALID_PARAM`，注入 peer ucid 的 7 组查询均不返回他人数据 | 主报告 WD-01；`idor-probe.json` A/B/C 组 | 单账号门防线有效，跨账号仍阻塞（**R4-B07，挂靠 WD-01**） |
| 文案 App 房源接口匿名可达仅需公开应用签名：`/falcon/house/zu/house_info?house_id=` 与 `/falcon/new_house/house_info?pid=` 签名后 200 返回完整房源字段（含 roles 经纪人信息） | `property-auth-audit-evidence-2026-11-20.json` L391-471、L634-729 | 匿名读面成立（主报告 KE-02/03 已证） |
| 签名秘密是公开 JWT 头常量 `{"alg":"HS256","typ":"JWT"}`（已 base64 解码验证），签名串 `METHOD+path+timestamp+nonce`，**不覆盖 query/body** | 离线切片 + copy-app `Ke()/Pi()`；主报告 KE-02/03 | 签名不构成个人鉴权 |
| 畸形 `house_id=0` 返回 400（68B 业务错误）而非 401/403，说明请求穿过签名门进入业务校验；r3 同路径 CORS 探针还回显 Python 内部异常 `'NoneType' object has no attribute 'get'`（KE-15） | `r3-runtime-probe` P26 记录 | 业务层可达 |
| `city/list` 对 garbage/missing X-App-Id 均 200（4 组同体 29458B） | r3 P26-quota-* | 仅证公开字典路由不校验 App ID；**计费端点配额归属仍阻塞，见 r3 报告 P2-6** |

**攻击推演（不执行 ±1 枚举，仅离线建模）**：`house_id`/`pid` 为连续短整数 + 匿名公开签名 + 无个人鉴权，三条件同时成立时批量遍历的边际成本仅为签名计算（HMAC-SHA256，客户端微秒级）。即使 ID 不连续，6 位数字空间仅 90 万，命中率问题不构成访问控制。**正确防线只能是服务端对象级鉴权（BOLA）+ 网关速率限制**，ID 不可预测性不得作为安全依赖。注意：本节不再把「历史 0 个 429」作为并列前提——该观测的采样口径过弱（见 §4.1），限流缺口的强证据是主报告 M4 并发突发与 KE-01 翻页。

- **R4-B06（阻塞待证，挂靠 KE-02/03，不另立发现编号）房源匿名遍历面量化**：公开应用签名与无个人鉴权已由 KE-02/03 证明，「编号连续性 + 遍历可行性」受红线阻塞；限流维度以 M4 突发证据为准而非 0/429 台账。`r4_idor_token_probe.py` T1 用 0/非数字/随机大值 canary 做状态机与突发覆盖测试，**不触碰相邻 ID**，可在不违反红线前提下量化 429 覆盖与错误信封差异。
- **R4-B07（挂靠 WD-01）豌豆跨账号 BOLA**：现有单账号证据支持「会话/笔记/客源不跨主体」，但 `noteId` 直链 `/api/v2/notes/detail/{id}` 的跨账号随机 canary 测试仍待第二账号（T3 harness 已备，`R4_WANDOU=1` + 恢复凭证）。

### 2.2 分享 Token 与邀请码（Moma 6 类面）

| 端面 | 契约（staging OpenAPI） | 匿名门运行时证据 | 本轮静态发现 |
| --- | --- | --- | --- |
| `/api/moma_agent/shared`、`/prd/shared`、`/prototype/shared`、`/research/shared` | GET 各 1 | — | 前端均以 `share_token`/`shareToken` 查询参数消费 |
| `/api/moma_agent/html-to-pdf/shared` | GET | **403 `{"detail":"分享链接无效或已过期"}`**（13B 随机 canary，`round2-anon` L1009-1053） | 服务端做 Token 存在性+有效期校验，非裸 401；枚举 oracle 收敛（防线有效复核） |
| `/api/moma_agent/message-ext-files/{id}/share-token` | POST（登录态签发） | — | 返回 `data.share_token`，配套 `/shared/detail?token=` 回读 |
| `/api/chats/{chat_id}/invite/generate` | POST（sample_post_paths 在列） | 匿名门预期 401 | 聊天协作邀请，chat_id 为路径参数 |
| `/api/invite/accept` | POST 1 | 待测 | 入参 `invite_code` |
| 用户 `inviteCode` | `/api/user/invite` GET；前端拼 `${origin}/invite/${inviteCode}` | 匿名 401（门待复测） | **inviteCode 是用户长期属性**，非一次性随机 → **R4-S04** |
| 视频创作 `/video-creation/invite` + `?avatarInvite=` | expert bundle 路由 | — | 另一套邀请体系 |

**熵基线（r4 工具 `entropy_bits()` 内置）**：128 bit 是任务红线。参照系：6 位数字仅 19.9 bit；8 位 Base62 约 47.6 bit；22 位 Base64URL 约 132 bit（达标）。前端 UUID 生成普遍走 `crypto.randomUUID()`/`getRandomValues`（CSPRNG，合格），但**分享 Token 由服务端签发，前端代码无法证明其熵与 TTL**——这是本面的核心未知。

- **R4-B03（新增阻塞假设）分享 Token 强度/TTL/二次使用未验证**：已知无效 Token 返回统一「无效或已过期」信封（不区分不存在与过期，枚举 oracle 收敛，防线有效），但有效 Token 的长度/字符集/熵、过期时间、撤销能力、错误尝试限流均无数据。解锁：登录态调一次 `share-token` 签发，内存中计算熵（不落盘），间隔 TTL 后回读验证失效与二次使用拒绝（T4 方法学已写入 dry-run 证据）。
- **R4-S04（新增静态线索·中）长期 inviteCode 与一次性邀请语义混用**：个人 `inviteCode` 随 `user/info` 长期下发并用于公开邀请链接（moma-pc-main 与 expert-main 同构切片：`${window.location.origin}/invite/${currentUser.inviteCode}`，先 `getUserInfo()` 再拼装），属用户长期属性而非一次性随机。
- **R4-B04（新增阻塞假设）accept 接受面限流未知**：若 `/api/invite/accept` 对长期 inviteCode 无失败限流/无过期/无使用次数上限，则可被在线猜测或撞库；其防护必须依赖「高熵生成 + accept 端限流 + 服务端租户归属校验」，不能依赖链接保密。T2 探针对 accept 做随机码错试突发（≤8 次）观察 429/锁定。
- **防线有效复核（不新增风险，原 R4-08）**：所有 bundle 的 UUID/随机 ID 生成路径优先 `globalThis.crypto.randomUUID/getRandomValues`，仅在旧环境 fallback 到 `Math.random`（GoTrue uuid4 polyfill 标准写法）；未发现用 `Math.random` 直接生成安全 Token 的业务路径。

---

## 3. CAS 登录前置接口客户端校验弱化（KE-11 深化）

### 3.1 已知事实与本轮新增

| 事实 | 来源 |
| --- | --- |
| `/authentication/initialize` 与 `/authenticate` 接受固定占位 `{"deviceId":"default","sign":"default"}`（任务基线，主报告 KE-11 低危） | 前期实证 |
| 全站登录页加载 314KB 风控 SDK `keRiskControl.js`，暴露 `window.RiskControlWithSign`，内含 Canvas/WebGL/Audio/硬件并发/字体等全套指纹采集与 AES、HMAC、sha256 原语、请求拦截器（`initInterceptors`、`requestRecorder`） | 离线 cas_risk 族 156 切片（工作产物） |
| SDK 是**增强型风控信号**而非强制门禁：占位值能走完 initialize 说明服务端不拒绝无真实指纹/签名的请求 | KE-11 实证 + SDK 架构 |
| 页面同时挂载 `ai-security-sdk.umd.js`、`dig-tracker`、`moma-sdk-prod`（含网络拦截/签名/代理重写） | r3-surface-fetch 资产清单 |
| moma-sdk 的限流（`rateLimitEnabled/rateLimitThreshold/rateLimitWindowMs`）与 HMAC 签名全部在 **XHR/fetch 包装层本地执行**，本地 Map 计数，触发时 `Request blocked` 仅发生在浏览器内（切片 @7841/@5561） | moma-sdk-prod 切片 → **R4-S02** |

### 3.2 攻击含义

1. 自动化脚本不加载任何 SDK，直接向 CAS 端点发 JSON 即可绕过全部前端指纹与本地限流；`deviceId/sign` 可恒为 `default`，撞库工具的请求构造成本为零。
2. 真实防线只能位于服务端：失败锁定、阶梯延迟、CAPTCHA 强升、异常 IP/设备风控。**当前没有任何运行时证据证明这些服务端机制在短信/账密分支独立生效**（KE-11 仅评级低危是因为占位值只证明「前置校验弱」，未证明后端无锁定）。
3. moma-sdk 本地限流可被直接删除 SDK / 用任意 HTTP 客户端绕过，**不能计入服务端防护纵深**；若运维监控以该 SDK 的限流上报为依据，会产生「已防护」错觉。

- **R4-B05（阻塞待证，KE-11 提级候选，不另立风险编号）撞库防护四要素未验证**：① 账密/短信分支独立失败锁定；② 阶梯延迟；③ CAPTCHA 强升；④ 异常 IP 拦截。`r4_cas_probe.py` 用合成 canary 手机号（合成测试号，非真实用户；具体号段仅作为探针脚本的环境变量默认值，不写入任何证据/报告文件）做 ≤6 次受控失败序列 + XFF 旋转臂（C4/C5），通过业务码/body_sha/耗时三轴判定锁定与延迟阶梯，全程不碰真实账号、不跑字典。是否提级以 C4/C5 运行时结果为准。
- **R4-B05 附属设计缺口（原 R4-10，中）风控 SDK 非强制**：建议服务端对 `riskToken/deviceId` 做「缺失即降级挑战」而非静默接受 default；风控信号缺失应触发 CAPTCHA 或严格限流，而不是与真实设备同权。

---

## 4. 后台限流/反爬开关的生产生效状态

### 4.1 量化基线（口径修正：255 条是可解析子集，0/429 是弱旁证而非强证据）

`r4_evidence_stats.py` 对 `evidence/` 下 JSON 做递归归集，**仅识别同时含 `item` 与 `http`/`transport_error` 字段的台账条目**，输出 `r4-evidence-stats.json`：

- **覆盖范围限定**：本次脚本扫描 44 个 round4 之外的历史 JSON 台账、归集 **255 条**记录，其中 **23 条 host 被归为 `"?"`**（结构缺 `url.host`）；一期部分非 `request_ledger` 结构的证据条目不被识别，**255 条不是全证据库请求量**——一/二/三期台账口径合计实际已超过 300 条（round2 匿名及跟进轮台账已列 95 请求，round3 新增 70 请求）。报告与台账中不得再写「全证据库 255 条」。
- **采样性质不支持「全站无限流」推论**：这 255 条严格执行「单端点单次请求、无重试、跨 15+ 主机」红线，本身远低于生产限流阈值；其中非成功响应占比很高：401×38、404×18、400×14、网络错误 `None`×8、403×6、422×1——大量请求在业务门之前就被拒绝，根本不进入限流计数面。**低频单次请求不触发 429 是正常现象，0/429 不构成全站限流缺失的强证据**。
- 该子集的状态分布：200×147、401×38、404×18、400×14、206×9、None×8、302×6、403×6、204×4、201×2、500×2、422×1；`Retry-After/X-RateLimit-*` 限流响应头观测 = 0（弱旁证，仅说明采样窗口内未见过限流头）。
- **限流/熔断缺失的强证据仍以主报告已有两项为准**：① **M4 五路并发 LLM 突发**（`round2-session-burst`，5 路并发 `chat/completions` 全 200、无 429，KE-30）；② **KE-01 员工目录翻页**（登录态连续翻页无截断）。本轮 255 条低频台账只作为补充旁证，不单独背书「熔断缺失」。

### 4.2 管理面开关的真实语义（前端路由表泄露的配置骨架）——R4-S01 主证据

| 管理面路径 | 前端 meta 描述与 Redis 键（离线提取） | 强制语义线索 |
| --- | --- | --- |
| `/api/admin/falcon-gateway-acl` | 「Falcon 网关接口访问控制（**deny/audit + enforce 开关**）；Redis：`falcon:acl:*`」 | **显式存在 audit 只观测不拦截模式与 enforce 双态** |
| `/api/admin/chat-api-ratelimit` | 「反爬策略灰度与轻应…」 | 灰度发布 |
| `/api/admin/data-asset-query-rate-limit` | 「moma-data table-rows 按用户每分钟限流；Redis：`moma_data:table_rows:rate_limit:*`」 | 仅覆盖 moma-data table-rows |
| `/api/admin/metric-block` | 「禁用指定指标 ID；Redis：`moma_data:metric:blocked_ids`」 | 黑名单模型 |
| `/api/admin/security_gray` | 「外网自动开通：**kill_switch + ucid 白名单 + 尾号灰度**」 | 白名单/尾号灰度 |
| `/api/admin/antispider-gray` | 反爬灰度（staging 3-4 操作） | 灰度 |
| `/api/chats/api-ratelimit/override` | 单用户/单聊天覆盖通道 | 可被用来下发例外 |
| `/api/admin/customer-sms-whitelist` | 访客短信登录手机号 Redis 白名单（6 操作） | 短信能力灰度门（R4-S03） |

### 4.3 判定（按新 taxonomy 重新归位）

- **R4-S01（新增架构线索·中）ACL/限流开关普遍存在 audit/enforce 双态与灰度语义**：多项管理面配置带「灰度/白名单/enforce 双态/按用户维度」语义，而静态面无法观测生产开关当前处于 audit 还是 enforce。结合 M4 并发全 200 的强实证，合理的失效假设为三种之一或叠加：① 规则仅灰度未全量；② enforce 处于 audit 模式（只记不拦）；③ 规则作用域不含 `/falcon/*` 网关路径（data-asset 限流明确只覆盖 moma-data table-rows，falcon-acl 是独立开关且其 enforce 状态未知）。**注意本条是配置侧根因线索，不是独立的限流缺失证据**；`r4_ratelimit_coverage.py` 用 9 个单元 × 直连/XFF 双臂的受控突发（每臂 ≤8）输出覆盖矩阵，授权窗口可直接定位哪些平面在生产无熔断。
- **R4-S02（新增架构事实·中）客户端限流冒充防线**：moma-sdk 与 OpenApiConcurrencyManagement chunk 在浏览器本地做并发/频率拦截（本地 Map 计数、`Request blocked` 仅浏览器内），可被任意非浏览器 HTTP 客户端整体绕过；AI 网关、房源接口的真实熔断必须在 `/falcon` 网关与服务端实现，并以 429 + `Retry-After` 对外可观测。
- **既有防线（不误报）**：staging 管理面 POST 门统一返回 `errno=401 未登录或登录已过期`（`staging_post_gates` 8/8），管理 RBAC 匿名门有效；问题在「开关的生产强制态」而非「管理接口裸奔」。

---

## 5. 本期条目总览（三层 taxonomy）

### 5.1 新增离线发现 R4-S01 ~ S04（前期报告无对应项，本期真正的增量）

| 编号 | 名称 | 评级候选 | 证据形态 | 修复/解锁要点 |
| --- | --- | --- | --- | --- |
| R4-S01 | falcon-gateway-acl 的 audit/enforce 双态开关（`falcon:acl:*`）与多项灰度/白名单管理面配置 | 中（架构线索） | expert-index 路由表离线切片（§4.2） | 配置巡检：ACL 切 enforce、灰度到期自动全量；`/falcon/*` 纳入规则作用域 |
| R4-S02 | moma-sdk 限流仅在浏览器客户端本地执行（本地 Map、`Request blocked` 不出浏览器），无法替代服务端熔断 | 中（架构事实） | moma-sdk-prod 切片 @7841/@5561（§3.1） | 熔断下沉网关/服务端，429 + `Retry-After` 可观测；不得把 SDK 上报计入防护纵深 |
| R4-S03 | customer-sms-whitelist 表短信能力可能处于白名单灰度 | 中（架构线索） | 管理面 chunk + Redis 白名单语义（§1.1） | 白名单放开前先全量启用频控；默认手机号拒绝不得消耗短信配额 |
| R4-S04 | inviteCode 是用户长期属性并用于公开邀请链接，与一次性邀请语义混用 | 中（静态线索） | moma-pc-main/expert-main 同构切片（§2.2） | 高熵生成 + accept 端限流（R4-B04）+ 服务端租户归属校验 |

### 5.2 阻塞待证假设 R4-B01 ~ B07（全部挂靠主报告编号或标注新假设，0 实时请求）

| 编号 | 假设 | 挂靠/性质 | 解锁条件 |
| --- | --- | --- | --- |
| R4-B01 | 短信爆破五要素（长度/TTL/错试/频率/XFF） | 主报告阻塞表 **M5 短信（`ROUND2_SMS`）**，与 KE-26 无关 | 授权手机号窗口跑 S1–S5 |
| R4-B02 | GoTrue `captcha_token` 可选 → 服务端是否不强制 | M5 附属假设 | S4/S5 观察风控标记 |
| R4-B03 | 分享 Token 熵/TTL/撤销/二次使用未验证 | 新增假设（主报告无编号） | 登录态 T4：测熵 ≥128bit、TTL、二次使用拒绝、错试限流 |
| R4-B04 | `/api/invite/accept` 对长期 inviteCode 的限流/次数上限未知 | 新增假设（主报告无编号） | T2 随机码错试突发 ≤8 次 |
| R4-B05 | CAS 撞库防护四要素未验证（含风控 SDK 非强制设计缺口） | **KE-11 提级候选** | C4/C5 失败序列；服务端锁定+延迟+CAPTCHA+IP 风控 |
| R4-B06 | 房源数字 ID 遍历面量化 | **KE-02/03** 探针跟进，不另立编号 | T1 canary 突发测网关 429，不碰相邻 ID |
| R4-B07 | 豌豆跨账号 BOLA | **WD-01** 跟进，不另立编号 | 第二账号 + T3 随机 canary |

限流生产覆盖矩阵（9 单元 dry-run）是 KE-30/M4 的验证工具而非新发现；其配置侧根因见 R4-S01。

### 5.3 旧 R4-01 ~ R4-12 编号处置映射（可追溯）

| 旧编号 | 处置 | 新归属 |
| --- | --- | --- |
| R4-01 短信爆破面 | 改阻塞假设 | R4-B01（M5） |
| R4-02 CAPTCHA 可选 | 改阻塞假设 | R4-B02 |
| R4-03 短信白名单灰度 | 认定为新增发现 | **R4-S03** |
| R4-04 房源遍历面 | 与主报告 KE-02/03 重复，降级为探针跟进 | R4-B06 |
| R4-05 豌豆 BOLA | 与 WD-01 重复，降级为跟进项 | R4-B07 |
| R4-06 分享 Token 强度 | 改新增阻塞假设 | R4-B03 |
| R4-07 inviteCode | 拆分为静态发现 + accept 阻塞 | **R4-S04** + R4-B04 |
| R4-08 前端随机源 | 防线有效复核，不编号 | §2.2 复核项 |
| R4-09 撞库四要素 | 与 KE-11 重复，改提级候选假设 | R4-B05 |
| R4-10 风控 SDK 非强制 | 并入 R4-B05 附属缺口 | R4-B05 |
| R4-11 限流覆盖缺口（0/255） | 降级：0/255 为弱旁证，强证据归 M4/KE-30；配置根因独立成新发现 | R4-S01 + §4.1 口径修正 |
| R4-12 客户端本地限流 | 认定为新增架构事实 | **R4-S02** |

**修复优先级**：

1. **P0（与主报告 P0 并行）**：`/falcon/*` 网关全量启用服务端速率限制与并发熔断（R4-S01/S02，强证据为 M4）；短信三件套（错试作废 + 发送间隔 + 双键限流）在白名单全量前落地（R4-B01/S03）。
2. **P1（1–2 周）**：CAS 服务端撞库防护四要素 + 风控信号强制化（R4-B05）；房源接口对象级鉴权复核（R4-B06）；分享/邀请 Token 服务端熵 ≥128bit、TTL、撤销与 accept 限流（R4-B03/B04）。
3. **P2（持续）**：限流可观测性建设——429 计数、`X-RateLimit-*` 响应头、audit/enforce 双态的配置巡检与灰度到期自动全量；第二账号 BOLA 回归（R4-B07）。

---

## 6. 探针执行手册（授权窗口）

| 脚本 | 解锁环境变量 | 真实请求上限 | 产出 |
| --- | --- | --- | --- |
| `r4_sms_probe.py` | `R4_RUN=1 R4_SMS_PHONE=<授权号> R4_SMS_LIVE_SEND=1` | 发送 ≤12、错试 ≤6 | `r4-sms-probe-live.json` |
| `r4_idor_token_probe.py` | `R4_RUN=1`（豌豆面再加 `R4_WANDOU=1 R4_OWN_NOTE_ID=<自有id>`） | 每端点 ≤8，无相邻 ID | `r4-idor-token-probe-live.json` |
| `r4_cas_probe.py` | `R4_RUN=1`（可选 `R4_CAS_ATTEMPTS=6`） | 失败序列 ≤6×2 臂 | `r4-cas-probe-live.json` |
| `r4_ratelimit_coverage.py` | `R4_RUN=1`（AI 费用面另需 `R4_AI_BURST=1`） | 9 单元 × 2 臂 × ≤8 | `r4-ratelimit-coverage-live.json` |
| `r4_evidence_stats.py` | 无需解锁，纯离线 | 0 | 统计台账（evidence/） |
| `r4_offline_extract.py` | 无需解锁，纯离线 | 0 | 切片工作产物（`outputs/security-round4-workproduct/`） |

统一红线：单端点硬上限 12（`HARD_BURST_CAP`）；手机号/canary 不落盘；URL query 值只留长度+单次运行随机盐 HMAC 前 16 位（低熵/PII 字段不输出哈希）；每轮证据先过统一脱敏门禁再归档（`scripts/security/redaction_gate.py`，递归扫描整个审计目录）；阻塞项在凭证/授权恢复前保持 0 请求，不计通过。

## 7. 与主报告的衔接（编号映射已校正）

- R4-B01/B02 与 R4-S03 对应主报告阻塞表的 **M5 短信项（`ROUND2_SMS`）**，本轮交付可直接执行的闭环探针；**主报告 KE-26 是渲染（html-to-pdf/image）/自定义 MCP/Groovy 测试面的阻塞项，与短信 M5 无关**，前期稿件「KE-26 M5 短信」为编号误写，本期更正。
- R4-B06 对应主报告 KE-02/03（公开签名/匿名读面）的量化跟进；R4-B07 对应 WD-01 与红线搁置的双账号 BOLA。
- R4-B05 是 KE-11（低危）的升级论证链，是否提级以 C4/C5 运行时结果为准。
- R4-S01/S02 与限流覆盖矩阵为主报告 KE-30/M4（并发熔断缺失，强实证）补充配置侧根因（audit/enforce 双态、灰度、作用域缺口、客户端本地限流）与全平面覆盖矩阵工具；0/255 低频台账仅作弱旁证，不替代 M4 的突发证据。
- R4-B03/B04 是主报告尚无编号的新增阻塞假设，在登录态授权窗口验证后再决定是否提级立项。
