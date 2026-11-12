# crm_connector

员工专属的 CRM MCP Connector 服务。它将语义化的 CRM 查询工具与认证/会话提供器隔离，默认不包含任何真实凭据（Cookie、Token、Ticket、二维码 Payload、员工 UCID）。`Settings` 的默认 `crm_login_base` 等指向厂商公开 SSO 主机（`login.ke.com`、`lease-pz.link.lianjia.com`），属厂商公开域名而非部署机密；覆盖方式见 `.env.example` 中的 `CC_*` 变量。

## 当前能力

> **完整链路已于 2026-08-06 用真实扫码验证打通**：扫码登录 → CAS 双跳 bootstrap → 凭证入库 → `authorizedFetch` 业务调用 → TGC 无感刷新。
> **MCP stdio 已于 2026-08-07 用真实凭据验证**：`crm-mcp` 子进程在 `kecom-prod` profile 下通过 `stdio` 暴露工具，`crm_whoami` / `rental_listing_search` / `rental_listing_get_detail` 均返回真实 CRM 数据。
> **买卖板块已于 2026-08-11 用真实凭据验证**：house.link shiro-cas 域（`saas_token` + `HOUSEJSESSIONID`）的搜索/筛选字典/小区联想/详情头/维护信息/跟进记录全部走通，`sale_listing_*` 系列 MCP 工具返回真实在售房源数据。

- `/api/v1/health`：服务健康检查；
- `/api/v1/connection/status`：认证与连接状态；
- `/api/v1/auth/login`：未登录时发起扫码登录（返回 `login_id` 与二维码内容），手机扫码确认后服务自动安装凭证并切到 `ready`；
- `/api/v1/auth/login/{login_id}`：轮询扫码登录状态（`pending` / `ready` / `failed` / `cancelled`）；
- `/api/v1/auth/login/{login_id}/qrcode.png`：当前二维码的 PNG 图片（可直接在浏览器展示）；
- `/api/v1/auth/login/{login_id}/cancel`：取消进行中的扫码登录；
- `/api/v1/mcp/tools`：与 MCP transport 同源的工具元数据（`app/mcp/tools.py`，经 `/tools/list` 暴露）；
- `/api/v1/crm/me`、`/api/v1/listings/rental/search`：已完成租赁房源的分层与输入/输出契约，走通真实上游 `KecomCrmClient` → `KecomSessionProvider.authorizedFetch` → `lease-pz.link.lianjia.com`。
- `/api/v1/listings/rental/{listing_id}`：租赁房源详情端点，返回单条 `RentalListingResponse`。
- `/api/v1/listings/sale/search`、`/filter-options`、`/suggest`、`/{listing_id}` 等：买卖（在售）房源查询端点，走真实上游 `house.link.lianjia.com`（详见 [`docs/sale-api-catalog.md`](docs/sale-api-catalog.md)）。
- `/api/v1/listings/sale/map/suggest`、`/listings/sale/map/nearby`：买卖地图找房——地点联想与半径范围查询（万象城 1 公里 → 44 小区 → 2000+ 套在售，2026-08-11 实测）。

## 服务内扫码登录

主服务（默认 `8020`）运行时若未登录，会**自动弹出原生二维码窗口**（Windows Tk，与 `crm-authd login` 同款），员工直接扫码即可，无需浏览器；由 `CC_QR_LOGIN_AUTO_START`（默认开）控制。实现见 `app/application/qr_login.py`（`QrLoginManager`）：

- 每次登录尝试使用独立的 `KeComQrBootstrapProvider`（全新 httpx client jar），多个尝试互不串 TGC cookie；同时只允许一个 `pending` 尝试，重复发起返回 `409 CRM_QR_LOGIN_CONFLICT`；
- 二维码过期后由 bootstrap 流程自动刷新并重新弹窗显示新码；
- 登录成功走 `install_fresh_credential`（validate → 原子保存 → 作废旧凭证）并校验 `CC_BOUND_EMPLOYEE_PRINCIPAL` 与扫码主体一致；
- 弹窗由后台线程驱动（`pump` 轮询 Tk 事件），扫码确认后自动关闭；
- 默认 `unconfigured` profile 下不弹窗，登录端点返回 `501 CRM_UPSTREAM_NOT_CONFIGURED`。

HTTP 端点仍可用作状态查询 / 替代展示（无需依赖浏览器即可完成登录）：

```powershell
# 1. 查看登录状态
curl http://127.0.0.1:8020/api/v1/connection/status
# 2. 手动触发扫码登录（默认会自动触发，端点同样可用）
curl -X POST http://127.0.0.1:8020/api/v1/auth/login
# {"login_id":"...","state":"pending","qrcode":"https://t.lianjia.com/...","note":"...","message":""}
# 3. 可选：浏览器打开二维码图片
curl -o qr.png http://127.0.0.1:8020/api/v1/auth/login/<login_id>/qrcode.png
# 4. 轮询直至 ready（手机确认后自动完成）
curl http://127.0.0.1:8020/api/v1/auth/login/<login_id>
# 5. 取消登录
curl -X POST http://127.0.0.1:8020/api/v1/auth/login/<login_id>/cancel
```

## crm-authd / crm-mcp 命令行

本服务除 FastAPI 主服务（默认 `8020`）外，还提供两个命令行入口：

- `crm-authd`：管理员工 CRM 认证材料并在本地保持会话，入口在 `pyproject.toml` 注册为 `crm-authd = "app.authd.cli:main"`，实现见 `app/authd/cli.py`；
- `crm-mcp`：MCP stdio 服务器，入口注册为 `crm-mcp = "app.mcp.server:main"`，实现见 `app/mcp/server.py`。

| 命令 | 作用 |
| --- | --- |
| `crm-authd login` | 触发扫码登录引导（弹出二维码窗口），拿到上游凭证后用 Windows DPAPI 写入 `CC_CREDENTIAL_STORE_PATH`，并校验 `CC_BOUND_EMPLOYEE_PRINCIPAL` 是否与扫码主体一致 |
| `crm-authd status` | 读取本地凭证并打印 `state`（`ready` / `expiring` / `auth_required`）、会话主体、过期时间、`credential_version` |
| `crm-authd logout` | 调用 `bootstrap.revoke` 失效上游会话并清空本地凭证记录 |
| `crm-authd serve` | 在 `CC_AUTHD_LISTEN_ADDRESS`（默认 `127.0.0.1:8021`）启动本地认证中心 HTTP 服务；默认不启动周期 keepalive（惰性校验，见下节），`CC_KEEPALIVE_INTERVAL_SECONDS>0` 时恢复周期探测线程 |
| `crm-mcp` | 在 `stdio` 上运行 MCP server（`app/mcp/server.py` 的 `main()`），按 `CC_UPSTREAM_PROFILE` 接线真实或 stub provider |

`crm-authd serve` 暴露的端点（前缀 `/api/v1/auth`）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/auth/status` | 当前认证状态、绑定员工、过期时间、最近一次 keepalive |
| GET | `/api/v1/auth/poll` | 与 `status` 一致，为后续接入 SSE / tray UI 预留 |
| GET | `/api/v1/auth/keepalive` | 手动触发一次上游活性探测（探测失败时自动 TGC 无感刷新）并返回结果 |
| GET | `/api/v1/auth/notify` | 预留给未来 tray UI 向 authd 推送通知（当前镜像 status） |

> 认证材料只存放在 `CC_CREDENTIAL_STORE_PATH`（默认 `./run/credential_store.bin`），用 Windows DPAPI 加密；`crm-authd` 之外的主服务进程通过 `SessionProvider.authorizedFetch()` 间接使用，从不直接接触原始 cookie/token。

## 惰性会话校验与浏览器一致请求头（2026-08-15）

为降低员工账户被上游风控标记为异常的风险，会话策略从"周期探测"改为"惰性校验"，并让出站请求头与真实工作台浏览器保持一致：

- **零闲置流量**：默认（`CC_KEEPALIVE_INTERVAL_SECONDS=0`）没有任何定时器访问上游。`crm-authd serve` 不再启动 KeepaliveTimer；主服务的 session watchdog 每 60 秒只做**本地**状态读取（不发上游请求）。
- **调用即校验**：每次 `authorizedFetch` 前检查本地过期估算，估算已过期先走一次 TGC 无感刷新（无扫码）；业务响应成功即证明会话存活，并将本地估算滚动续期（+1 小时，仅在剩余不足 30 分钟时写盘，且只作用于仍未被替换的当前凭据）。闲置会话不会被时钟误杀，也不会被探测唤醒。
- **状态读取纯本地**：`status`（`/api/v1/connection/status`、`crm_connection_status`、authd `/status`）不再触发刷新或探测；本地估算过期时报告 `expiring`，恢复发生在下一次真实调用。只有会话真正失效（调用被上游拒绝且 TGC 刷新失败）才进入 `auth_required`，此时 watchdog 自动弹出扫码窗。
- **手动探测保留**：`GET /api/v1/auth/keepalive` 仍可按需探测；设置 `CC_KEEPALIVE_INTERVAL_SECONDS>0` 可让 `crm-authd serve` 恢复周期探测。
- **浏览器一致请求头**：所有上游请求（业务、CAS 引导、keepalive）默认携带工作台同款 `User-Agent`（Windows Chrome）与 `Accept-Language: zh-CN,zh;q=0.9`，不再暴露 `crm-connector/0.1` 这类非浏览器签名。`chrome://version` 页面本身进程外不可读，但所需数据可自行获取：连接器默认**自动读取本机已安装浏览器版本重建 UA**（`app/infrastructure/browser_signature.py`：Chrome 注册表 `BLBeacon` 版本 → `Chrome/{主版本}.0.0.0`，无 Chrome 时回退 Edge 并附 `Edg/` 后缀，再退静态默认值；按 user-agent 缩减政策，浏览器真实发出的正是这种冻结小版本号的形态）。只有当你用便携版或非系统浏览器访问 CRM 时才需要用 `CC_HTTP_USER_AGENT` 强制指定。

长时间闲置后需要"预热"会话时，调用一次任意 MCP 业务工具即可同时完成校验与续期，无需专门的 keepalive。

### 已验证的端到端链路

| 环节 | 上游端点 | 验证结果 |
| --- | --- | --- |
| 扫码 bootstrap | `POST /authentication/authenticate` → CAS 双跳 | TGC + `puzu_lease_token` + `saas_token` 全部种下 |
| houseList 查询 | `GET /api/houseList/search/pc/list` | `code:100000`，返回 10 条真实房源（例 `ICC凯旋门 月租3850元`） |
| keepalive | `GET /api/puzuHouse/.../accountRightInfo?typeList=2` | `code:100000`，`ConnectionState.READY` |
| TGC 刷新 | CAS 双跳（无扫码） | 新 `puzu_lease_token` + `saas_token` 种入，`BootstrapResult` 正常返回 |

## 租赁房源本地化（默认开启）

覆盖成都 CRM「全部房源」**全部 24 个 `districtId`**（12 个主城/近郊 + 高新、青白江、新津、都江堰、简阳、金堂、崇州、彭州、大邑、邛崃、蒲江、天府新区南区），游标按区循环：每轮都重新扫列表（增量），任何区或租金档首页 `total > 1000`（上游深分页硬顶）就递归切开——先把租金档对半分，1 元档仍超 1000 再按 `delType`（普租 2 / 托管 5）切——直到每个单元都完整可达。**已本地化且未过期的房源不再打详情**（TTL 14 天，列表行内容变化才重采）。普租跟进随详情归档（已入库且缺 `follows` 的普租，后续列表扫到时补采一次）；具体楼层、详细地址仍不请求。游标按区循环，因此长跑即「增量 + 全量」：新区自动补采，旧区每轮复核。

Connector 启动即归档。关闭：

```
CC_RENTAL_ARCHIVE_ENABLED=0
```

清水房采集默认关闭；不要和本归档同时打开去打同一 CRM 账号。

库文件默认 `./run/rental_archive.sqlite3`（`CC_RENTAL_ARCHIVE_DB_PATH`）。Asia/Shanghai **仅工作时间 09:00–21:00 作业**，21:00–09:00 暂停。每套详情本地化后只停留 **2 秒**；列表翻页仍 8–25 秒。默认**不限制**日请求/详情封顶（`CC_RENTAL_ARCHIVE_DAILY_REQUEST_CAP=0`、`CC_RENTAL_ARCHIVE_DETAIL_DAILY_CAP=0`）。

看板：浏览器打开 [http://127.0.0.1:8020/archive](http://127.0.0.1:8020/archive)（每 2 秒刷新，不含楼层/地址）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/archive` | 本地化状态看板 |
| GET | `/api/v1/archive/rental/status` | 开关、游标、分区统计、当日计数、最近一轮 |
| POST | `/api/v1/archive/rental/tick` | 执行一单位工作（一页列表或一套详情） |
| GET | `/api/v1/archive/rental/listings` | 已归档列表（可按 `district_id` / `active` 过滤） |
| GET | `/api/v1/archive/rental/listings/{id}` | 单套已归档 payload（已剥离敏感字段） |

## 买卖房源本地化（默认开启）

与租赁归档并行的独立归档：**爬取范围与租赁本地化相同的 14 个区**（主城/近郊，外加天府新区、高新西、高新、天府新区南区，游标按同一顺序在这 14 区间循环），并同样跳过青白江、新津、都江堰、简阳、金堂、崇州、彭州、大邑、邛崃、蒲江这 10 个远郊区县；始终用 `vertical=all`（不限，绝不发 `gdiv_mt`）；价格是**总价万元**，上游固定每页 **30 行**（pageSize 被忽略、不发送）。某单元首页 `total > 1000`（与租赁列表同一深分页硬顶）即递归切分——先总价、再面积、最后室数——用尽切分维度仍超 1000 则只采可达的 1000 行且不做失效下架。独立 runner、SQLite（默认 `./run/sale_archive.sqlite3`，`CC_SALE_ARCHIVE_DB_PATH`）与游标，与租赁归档**共用同一个 CRM 账号**和 09:00–21:00 窗口及延迟/封顶默认值；HTTP 路由同构于 `/api/v1/archive/sale/`（status/tick/listings/listings/{id}）。

## 归档视觉持续处理（archive_vision）

视觉命令与出租、出售归档进程独立，源库只读，不访问 CRM，不改 payload_json、content_hash、detail_status、计数或游标。Hermes 不在维护和验收范围。

- 默认每页最多扫描 500 行，以 listing_id 主键分页。视觉库小型元数据表保存租售公平轮转进度；预算用完或中断后继续，同一房源多张图也可续跑。不加载全库 payload，不在模型调用期间持有源库快照。新增 ID 或旧房源换图最迟在下一轮完整扫描发现；这是周期扫描，不能保证实时。
- --limit 是整个命令的图任务上限，下载失败也占额度；--batch-size 是每批额度，二者独立。缓存命中不占额度，未尝试的图不写 error。--backfill 无 limit 时完成当前扫描轮次后退出，--watch 持续轮转；分页游标不是 CRM 归档游标。
- 串行请求，一个 NVIDIA_API_KEY（兼容 NIM_API_KEY），默认每次请求至少间隔 4 秒，重试也限速；HTTP 超时 22 秒、429 退避可中断。不经过 LiteLLM/OpenRouter。模型、提示词和关闭 thinking 的调用保持固定。
- ok/miss 缓存按 model/prompt/schema 隔离，同字节 SHA 共享识别；规则变化只重算。error 冷却 15 分钟。推理后重新读取当前 URL/活动状态；换图保持 stale，下架/删除不参与筛选。删除 ID 另做有界核验，不依据未扫描到的分页数据判定下架。
- OS 文件锁覆盖整个持续进程和离线重建，重复启动立即失败；SIGINT/SIGTERM 停止接新图，当前下载/请求有界结束。默认至少保留 2 GiB 空间，不足时 watch 输出 paused_storage，至少间隔 60 秒复查；有限小批测试可显式下调阈值。程序不会删除日志或图片。
- labels 为空或不可读是 miss，设施 NULL。筛选仅 ready 的确定值；NULL 不进入“要”或“不要”的结果。实拍槽标记和车位标记保留，推断用途冲突不作为确定用途。

从 services/crm_connector 运行，key 只放环境变量：

```powershell
python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --sale-db ./run/sale_archive.sqlite3 --vision-db ./run/archive_vision.sqlite3 --backfill --limit 20
python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --sale-db ./run/sale_archive.sqlite3 --vision-db ./run/archive_vision.sqlite3 --backfill
python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --sale-db ./run/sale_archive.sqlite3 --vision-db ./run/archive_vision.sqlite3 --watch --batch-size 20 --poll-interval 60
python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --sale-db ./run/sale_archive.sqlite3 --vision-db ./run/archive_vision.sqlite3 --rebuild-derived
```

离线重建不需要 key、下载或模型。运行前可用 SQLite Backup API 备份视觉库。小批连续两次可能处理不同的待补图片；检验缓存幂等应固定同一房源/图片集合，不能要求下一批待补图请求数为零。

部署模板 deploy/archive-vision.service 使用服务器现有 ubuntu 用户、venv、独立 vision-current 目录和独立环境文件（root:root 0600）。CPUQuota=20%、MemoryMax=384M、Nice=10；不更换 /opt/crm-connector/current，不重启 crm-connector.service。安装后用 systemctl start/stop archive-vision；先小批验收和检查磁盘再 enable。

## 本地运行

Windows 长跑入口会先确认云枢（`YunShu.exe`）已启动，没有则拉起 `YunShuLauncher.exe`，再启动 Connector：

```powershell
..\..\scripts\start_crm_connector.ps1
```

直接跑 uvicorn：

```powershell
cd services/crm_connector
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8020
```

默认 `CC_UPSTREAM_PROFILE=kecom-prod`：服务启动即接线真实 ke.com SSO，未登录（`auth_required`）时自动弹出原生二维码窗口，扫码确认后凭证入库、会话变 `ready`。CI/纯本地开发可用 `CC_UPSTREAM_PROFILE=unconfigured` 换回 stub provider 直接跑通；`crm-authd login` / `crm-authd serve`（监听 `127.0.0.1:8021`）仍可单独用于扫码引导与会话保持。

## MCP 接入

`crm-mcp` 以 stdio 运行 MCP server，供本地 Agent（Claude Code 等）直接调用：

```json
{
  "mcpServers": {
    "crm-connector": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "C:\\path\\to\\services\\crm_connector",
      "env": {
        "CC_UPSTREAM_PROFILE": "kecom-prod",
        "CC_BOUND_EMPLOYEE_PRINCIPAL": "<扫码主体>",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

- 工具清单（全部只读）：`crm_connection_status`、`crm_whoami`、`rental_listing_*`（搜索/筛选字典/详情/实勘/房源信息）、`rental_map_*`（联想/附近）、`tuoguan_listing_*`（待出租库存搜索/详情/成交参考）、`sale_listing_*`（搜索/筛选字典/小区联想/详情/详情头/维护信息/跟进记录）、`sale_map_*`（买卖地点联想/附近范围查询）；
- `crm_connection_status` 不限额；其余 MCP 工具按调用者主体（`getpass.getuser()`）受 `CC_MCP_RATE_LIMIT_PER_MIN`（默认 30 次/分钟）滑动窗口限流，超限返回 `RATE_LIMITED`；
- 未配置凭据时业务工具返回 `CRM_AUTH_REQUIRED`，不会访问上游；
- 凭据只经 `SessionProvider.authorizedFetch()` 注入，MCP 响应与日志不含任何认证材料。

命令行验证与调用样例：`examples/mcp_client.py`（`status` / `whoami` / `search` / `detail` / `demo` 全链路子命令），不依赖 Claude Code：

```powershell
CC_UPSTREAM_PROFILE=kecom-prod python examples/mcp_client.py demo --keyword 万象城
```

仓库根 `.mcp.json` 已将 `crm-connector` 注册为项目级 MCP server（stdio + `kecom-prod` profile），重新启动 Claude Code 后即可直接使用 `crm_*`、`rental_listing_*` 和 `sale_*` 工具。

> Dockerfile 用于服务契约与基础运行验证；生产 Connector 仍应运行在云枢已接入的员工专属 Windows VM 中，不应把云枢或员工认证材料打包进镜像。

## 分层

```text
api -> application -> domain <- infrastructure
```

- `domain/providers/credential_bootstrap_provider.py` 是认证材料引导边界；
- `domain/providers/session_provider.py` 是已认证请求边界；
- `domain/providers/credential_store.py` 是认证材料的唯一持久化边界；
- `domain/providers/crm_client.py` 是 CRM 业务接口边界；
- `infrastructure/kecom_qr_bootstrap.py`：扫码 bootstrap + CAS 双跳 + TGC refresh 实现（`KeComQrBootstrapProvider`）；
- `infrastructure/kecom_session_provider.py`：已认证请求代理与 keepalive，注入 `puzu_lease_token` + `saas_token` 等业务 cookie（`KecomSessionProvider`）；
- `infrastructure/kecom_crm_client.py`：CRM 业务 Adapter，通过 `SessionProvider.authorizedFetch` 调用受控路由（`KecomCrmClient`）；
- `infrastructure/windows_dpapi_credential_store.py`：Windows DPAPI 加密本地存储；
- `mcp/tools.py`：21 个 MCP tool 的契约（输入/输出 Schema、只读标注），HTTP `/api/v1/mcp/tools` 返回与 stdio `/tools/list` 一致的扁平 Schema；
- `mcp/server.py`：MCP server 组装（`build_mcp_server`）与 `stdio` 入口（`main`），注册连接、租赁和买卖查询工具，所有业务工具使用 `ToolAnnotations(read_only_hint=True)` 并按调用者主体限流；
- `mcp/schemas.py`：MCP 入参模型（`extra="forbid"` 拒绝未知字段）；
- `mcp/rate_limit.py`：滑动窗口工具级限流。

认证流程固定为：`CredentialBootstrapProvider → CRM 主体验证 → CredentialStore → SessionProvider.authorizedFetch()`。
业务 Adapter 只能通过 `SessionProvider.authorizedFetch()` 发起已认证请求。不得将认证材料存入 SQLite、普通文件、环境变量、日志、MCP 响应或前端页面。

### CAS 双跳认证链路

一次扫码完成全部业务调用所需的 cookie 种植：

1. **lease-pz CAS hop**：`GET login.ke.com/login?service=<lease-pz/login?gotoURL=...>` (带 TGC) → 302 → `lease-pz/link?.ticket=ST` → Set-Cookie `puzu_lease_token` / `UCID` / `csrfSecret`。
2. **house.link shiro-cas hop**：`GET login.ke.com/login?service=house.link.lianjia.com/shiro-cas` (同一 TGC) → 302 → `house.link/shiro-cas?ticket=ST` → Set-Cookie `saas_token` / `HOUSEJSESSIONID`。

仅有 `puzu_lease_token` 时 `accountRightInfo`/`houseList` 返回 `100001`（请重新登录）；补上 `saas_token` 后翻成 `100000`。两跳共用同一 `TGC`，零额外扫码。

终端二维码收到上游的过期状态后，`crm-authd login` 会自动重新初始化并展示新二维码。刷新初始等待时间由 `CC_BOOTSTRAP_QRCODE_REFRESH_INITIAL_DELAY_SECONDS` 控制，连续失败时采用最多 30 秒的指数退避。

## 归档视觉识别与离线重建 (Archive Vision)

离线户型图视觉识别流水线与规则重算工具（`app.application.archive_vision`）：

### 简明命令行用法

1. **运行推理流水线**（联网下载原图并调用模型打标）：
   ```powershell
   python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --vision-db ./run/vision.sqlite3
   ```
2. **离线重建/规则重算**（不联网，基于已缓存观测重新计算 derived 字段与汇总）：
   ```powershell
   python -m app.application.archive_vision --rental-db ./run/rental_archive.sqlite3 --sale-db ./run/sale_archive.sqlite3 --vision-db ./run/vision.sqlite3 --rebuild-derived
   ```

### 备份与安全说明
- 本地 Vision 数据库（如 `./run/vision.sqlite3`）可随时通过标准 SQLite 备份（`sqlite3 vision.sqlite3 ".backup backup.sqlite3"`）安全备份。
- 所有推理及离线重建操作均不修改原始 `run/*.sqlite3` 业务存档库（源库以只读 `mode=ro&query_only=true` URI 打开）。
- 未配置 `NVIDIA_API_KEY` 或 `NIM_API_KEY` 时调用推理流水线将提前报错退出，不会发起任何网络图片下载或模型调用。

详细架构与实施计划见 [`docs/personal-crm-mcp-connector-plan.md`](../../docs/personal-crm-mcp-connector-plan.md)。

认证流程实测结果与协议固化见 [`docs/crm-auth-flow-analysis.md`](../../docs/crm-auth-flow-analysis.md)：覆盖 `authentication/initialize`、`qrcode/query`（CREATED→BINDING→CONFIRMED→EXPIRED 状态机）、CONFIRMED 后 `lease-pz.link.lianjia.com/login?...&ticket=...` 的 cookie 注入、业务域受控路由 `GET /api/houseList/search/pc/list` 的请求与响应契约。该文档不含任何真实凭据，是后续 `CredentialBootstrapProvider`、`SessionProvider.authorizedFetch`、CRM HTTP Adapter 实现的事基准。
