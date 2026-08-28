# store_media

门店多媒体广告发布、房源轮播与角色管理微服务。服务可独立部署，默认监听 `8010`。

## 功能

- 管理员、区域经理、店长上传和维护所属范围内的图片或视频，上传时默认立即发布；
- 图片可设置 1–3600 秒停留时长，视频播放完毕后自动切换；
- 标题、图片时长、顺序、发布状态和待删除项通过一个事务统一保存；
- 展示端不叠加任何文字，每 30 秒同步一次清单，图片底边显示低亮度进度条；
- 展示端预加载并复用媒体节点，已发布文件使用不可变资源缓存，避免每轮切换重复加载；
- 系统管理员、区域经理、店长、店员四级 RBAC，权限按区域和门店隔离；
- SQLite 元数据、密码 PBKDF2 哈希、随机不透明会话令牌、本地媒体持久化；
- 管理页 `/`，门店展示页 `/display.html?store_id=<门店标识>`，精选房源大屏 `/featured.html`，OpenAPI `/docs`。

## 本地启动

```powershell
cd services/store_media
$env:SM_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:SM_BOOTSTRAP_ADMIN_PASSWORD="请设置至少8位的随机密码"
python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8010
```

首次成功启动会在空数据库中创建系统管理员。随后应移除引导账号环境变量；已有用户时不会重复创建。
生产环境建议把 `SM_STORAGE_DIR` 指向持久卷。SQLite 适合单实例初期部署；多实例部署前应按架构文档迁移至 PostgreSQL 和对象存储。

## 权限边界

| 角色 | 管理范围 |
| --- | --- |
| 系统管理员 | 全部区域、门店、账号和内容 |
| 区域经理 | 所属区域内门店、店长、店员和内容 |
| 店长 | 所属门店店员和内容 |
| 店员 | 登录并查看所属门店，无发布权限 |

## 毛坯房浏览（内部）

手机页面：`/roughcast.html`。它只展示 CRM 租赁房源中的“毛坯”结果：
服务端固定请求 `scope=all`、`fitment=002`，每页 30 条；页面下拉到末尾后按页码自动加载，
在列表顶部下拉可刷新。页码之外不接受任何页面筛选参数。点击房源卡片可查看该房源的 `REAL` 实勘图片，图片由
展示服务代理、清洗后返回，浏览器不会接触 CRM 凭证或实勘上传人等内部字段。

展示服务通过 `SM_CRM_CONNECTOR_BASE_URL`（默认 `http://127.0.0.1:8020`）调用已认证的
`crm_connector`；浏览器不会获得 CRM 凭证。可用 `SM_ROUGHCAST_CACHE_SECONDS` 调整服务端
缓存时间，默认 60 秒。

## 清水房采集（第 1 期，默认关闭）

按 [`docs/roughcast-quality-ranking.md`](docs/roughcast-quality-ranking.md) 的第 1 期范围，
本服务内置清水房全量采集链路（队列 A）。它与上面的展示接口互不影响：
展示走实时请求 + 内存缓存，采集写自己的 6 张 `roughcast_*` 表。

**默认不跑。** `SM_ROUGHCAST_CRAWL_ENABLED` 未打开时不起线程、不发一次上游请求。
头几天先手动跑，确认上游流量曲线之后再打开本地日 loop：

```powershell
python ..\..\scripts\roughcast_crawl_once.py --dry-run   # 只打印装配参数，零上游请求
python ..\..\scripts\roughcast_crawl_once.py             # 真跑一轮队列 A，约 66–76 次请求 / 40–90 分钟
python ..\..\scripts\roughcast_crawl_once.py --status     # 只读盘点，零上游请求，可随时跑
```

`--status`（可带轮数，如 `--status 20`，默认 10）把第 1 期出口条件要盯的东西一次读出来：
近 N 轮的 run 摘要（含 `毛坯` / `新快照`，用来看 4.5 的变更点写入是否真的在省快照）、
上游 `total` 的样本与极差（§七.7 的波动观察）、按本地日汇总的实际流量（请求数、相邻请求
间隔的 min/中位/max、长停顿次数、破间隔与窗口外次数）、熔断记录，以及三条出口条件的攒进度。
它**只 SELECT**——不建表、不写 `crawl_log`，所以跑多少遍都不影响当日预算。
出口条件与日流量固定按近 7 个本地日算，不受 `N` 影响。

### 本地日 loop（无人值守长跑）

打开 `SM_ROUGHCAST_CRAWL_ENABLED=1` 后，`store_media` 进程会顺带起一个名叫
`roughcast-daily-loop` 的线程。它**每天**按 Asia/Shanghai 本地日做：

1. 队列 A（如果今天**还没起过** A run；任何状态都算，包括手动 `roughcast_crawl_once.py`）
2. Shadow score（如果 A 已发布且最新 COMPLETE score run 没覆盖到该 A run）
3. 队列 B 每日配额（如果今天还没起过 B run 且仍在窗口内）

判定全部走 SQLite `roughcast_crawl_runs` / `roughcast_score_runs`——同一日内
即使把进程杀了再启，loop 也不会再起第二轮 A，也不会重复写 score。**`queue_a.start()` 与
`roughcast-daily-loop` 二选一**:loop 起来时不再另起 A 的 daemon,否则同日会跑出两轮 A。

本地长跑入口(`scripts/start_store_media.ps1`，模仿 `start_crm_connector.ps1`):

```powershell
..\..\scripts\start_store_media.ps1
# 已在监听 8010 时直接打印 "already listening" 并 exit 0，不杀活进程
# 否则在隐藏窗口里跑 uvicorn，日志在 services\store_media\run\store_media.{out,err}.log
```

排错要点:**如果 8010 已经被一个老进程占了**，先停掉它（任务管理器 / `Stop-Process`）再
重跑脚本,这样新代码里的 daily loop 与 ranked API 才会被加载。**云端 systemd / docker-compose
不要改**——`scripts/start_store_media.ps1` 是 Windows 本地专用的。

一轮 = 一个 `roughcast_crawl_runs` 行，终态有 `COMPLETE` / `PARTIAL` / `ABORTED` / `FAILED`，
其中 **`COMPLETE` 与 `PARTIAL` 都会发布**（刷新 `roughcast_listing_current`、写变更点快照、判下架），
发布的全部动作在一个事务里。中止或失败的一轮，数据止步于 `roughcast_crawl_stage`，
对 current / snapshot 一个字节都不碰。

上游负载控制（全部经由 `roughcast_throttle`，任何新增请求类型自动计入）：

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `SM_ROUGHCAST_CRAWL_ENABLED` | `0` | `roughcast-daily-loop` 开关。关闭时仍可用上面的手动入口 |
| `SM_ROUGHCAST_SCORE_AFTER_CRAWL` | `1` | A 之后是否跑 Shadow Run(0 则只采不评) |
| `SM_ROUGHCAST_QUEUE_B_DAILY_LIMIT` | `60` | 队列 B 每日小区配额,0 当天不跑队列 B |
| `SM_ROUGHCAST_DAILY_REQUEST_CAP` | `260` | 当日请求硬顶。任何推导预算都不得越过 |
| `SM_ROUGHCAST_MIN_REQUEST_INTERVAL_SECONDS` | `20` | 全局最小请求间隔 |
| `SM_ROUGHCAST_CRAWL_WINDOW` | `09:30,19:00` | 采集窗口（本地时间），出窗口即判 `ABORTED` |
| `SM_ROUGHCAST_CRAWL_PAGE_SIZE` | `50` | 每页条数。页数由第 1 页的 `total` 现算，不是常数 |
| `SM_ROUGHCAST_CRAWL_SAFETY_FACTOR` | `1.15` | 预算安全系数 |
| `SM_ROUGHCAST_CRAWL_RETRY_RESERVE` | `10` | 重试预留次数（重试同样扣预算） |
| `SM_ROUGHCAST_PAGE_GAP_SECONDS` | `25,90` | 页间随机停顿 |
| `SM_ROUGHCAST_LONG_PAUSE_EVERY_PAGES` | `8,15` | 每隔多少页插一次长停顿 |
| `SM_ROUGHCAST_LONG_PAUSE_SECONDS` | `180,480` | 长停顿时长 |

当日已花额度**从 `roughcast_crawl_log` 现算**，不是内存计数器——进程重启不会忘记今天的账。
遇到 429、`CRM_AUTH_REQUIRED`、`CRM_UPSTREAM_CHANGED` 或连续 3 次失败即熔断：当天剩余任务
全部取消并告警，绝不重试打穿。采集阶段不打详情接口（`RoughcastCrawlClient` 只有搜索方法）。

## 清水房参考集（第 3 期队列 B，默认关闭）

队列 A 只采到被排序集 P（毛坯）。评分要的参考集 R 由队列 B 按小区 `resblock_id` 搜索、
**不带 fitment 过滤** 产出，写入 `roughcast_community_reference_snapshot`。
指针 `reference_run_id` 只在 COMPLETE / PARTIAL 时前移，禁止 `max(run_id)`。

第一次请一次性扫完还没有参考集的小区（每小区单独收尾，中断后续跑会续上）：

```powershell
python ..\..\scripts\roughcast_queue_b_once.py --full-sweep --dry-run
python ..\..\scripts\roughcast_queue_b_once.py --full-sweep
python ..\..\scripts\roughcast_queue_b_once.py --status
```

全量模式抬高当日硬顶、关掉长停顿与采集窗口，**保留 20 秒最小间隔**。
约 1400 个小区、1.5 页/小区，按 20 秒间隔大约 8–12 小时。日常半月轮转用 `--limit 60`。

覆盖率复算（零上游请求，对照修正前/后口径）：

```powershell
python ..\..\scripts\roughcast_coverage.py
python ..\..\scripts\roughcast_shadow_score.py --dry-run
python ..\..\scripts\roughcast_shadow_score.py
```

排名页 `/roughcast-rank.html` 的「分享」会拼一张长图 PNG：房源要点、成都全市静态地图（百度 AK，红点为小区相对位置）、全部 REAL 实勘图。接口 `GET /api/v1/display/roughcast-ranked/{listing_id}/share`。需要 `SM_BAIDU_MAP_AK` 才能出地图；实勘走 crm_connector。

Shadow Run 只写 `roughcast_listing_scores`，手机页 `/roughcast.html` 不读分数。
内部核分页（封面图 + 最高/最低/极端/对照）：`/score-review.html`。

## 清水房本地自用排名（Phase 5 first cut）

`/roughcast-rank.html` 是 Shadow Run 的本地自用排名榜——经纪人按优质指数选房
的入口。**只读 SQLite，零上游**：不调用 CRM，不改采集 / 评分。请求路径是
`GET /api/v1/display/roughcast-ranked`，依赖最新 COMPLETE score run；没有就
404 `"还没有完成的评分批次"`。分组（`scored` / `nearby_estimate` /
`insufficient` / `data_error`）互不相交，分页 / 排序 / 行政区筛选 / 高可信
捡漏(`deals=true`)与现有 review 共用工具函数。

`/roughcast.html`（手机页）仍走 CRM 实时数据 + 内存缓存，**不读分数**。
`/roughcast-rank.html` 是另一条路径——选房用，零上游；展示用，仍走 CRM。
打开工作台的 `POST /api/v1/display/roughcast-score-review/{id}/open` 是两者
共用的入口，看板 view count 也走同一张 `roughcast_review_views` 表。

**ranked 页只读最新 COMPLETE score run**——本地日 loop 当天哪怕 A 跑了、score 还没
写完，ranked 页也仍用昨天的旧分数，采集失败不会把页面清空。云端推送仍是第 5 期
待办。

## 测试

```powershell
python -m pytest
```

## 服务器发布

生产 systemd 单元默认监听 `8080`，部署目录、持久化目录和验证命令见 [`deploy/README.md`](deploy/README.md)。
Docker Compose 同样默认将宿主机 `8080` 映射到容器 `8010`，可通过 `SM_PUBLIC_PORT` 修改宿主端口。
